"""两阶段审查管线（Phase 12：发现-验证分离的 plan-and-execute 架构）。

与单 Agent 循环（review.py 的 _review_with_agent）的结构差异：
- 阶段 1（发现）：一次无工具 LLM 调用通读代码，产出显式清单
  （审查 = 嫌疑清单；迁移 = 用法点清单）。粗扫产出不再随对话推进丢失。
- 阶段 2（验证）：程序 for 循环逐条执行"选择性检索 + 单次确认调用"。
  循环由代码驱动：检索次数 = 清单长度，天然有限，无需工具计数器、
  [终止] 信号、递归上限等 Agent 护栏（护栏随决策权一起退役）。
- 每次确认只带单文件上下文 + 本条证据：注意力集中，无无关文档稀释。

Migration 双向对照：
- 文档方向 = 原 Agent 路径（search_migration_changes 检索变更 -> 对照代码）
- 代码方向 = 本管线的 run_migration_code_direction（枚举用法点 -> 逐条区间检索）
- 合并规则（merge_bidirectional）：双侧命中 -> confidence=high；
  仅文档侧 -> medium（需人工复核代码位置）；仅代码侧 -> low（待商榷）。

降级链（宁可诚实降级，不做错误兜底）：
- 审查阶段 1 失败 -> 整体回退 Agent 路径（调用方负责）
- 单条检索失败 -> 降级为无证据确认（source=llm_inference）
- 单条确认异常 -> 跳过该条并记日志，不中断循环
"""
import json
import re
import time

from app.graph.state import ReviewState
from app.models.schemas import Suspicion, UsagePoint
from app.services import retrieval


# ===== JSON 提取（宽容解析：```json 块优先，裸大括号兜底）=====


def _extract_json(text: str) -> dict | None:
    """从 LLM 回复中提取 JSON 对象，失败返回 None（不抛异常）。"""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    if not text.strip().startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _format_docs(results: list[dict]) -> str:
    """把检索结果拼成与 Agent 工具层一致格式的证据块（[来源: ...]）。"""
    return "\n---\n".join(
        f"[来源: {r['source']} | 相似度: {r['score']}]\n{r['content']}" for r in results
    )


def _code_text(state: ReviewState) -> str:
    """把 code_context 拼成与 Agent 路径 _build_human_content 一致的代码文本。"""
    return "\n\n".join(
        f"=== 文件：{rel} ===\n{content}"
        for rel, content in state["code_context"].items()
    )


def _dedupe_issues(issues: list[dict]) -> list[dict]:
    """按 (file, title) 去重：同一问题不会因清单条目重叠而重复报告。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for issue in issues:
        key = (issue.get("file", ""), issue.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


# ===== 阶段 1 提示词：侦察（只发现，不下结论）=====

REVIEW_SCAN_PROMPT = """\
你是一名代码审查侦察员。通读以下项目代码，从三个维度找出所有值得进一步核实的\
问题嫌疑：api（API 用法是否合规/是否过时）、security（安全漏洞）、\
robustness（健壮性）。

要求：
- 只列嫌疑，不下结论：后续会逐条检索证据核实，宁可多列存疑，不要漏掉
- api 维度的嫌疑只针对下面列出的已确认技术；security 维度不受此限制
- 与检索核实无关的纯风格问题（命名、注释、格式）不要列
- file 必须使用代码中标注的相对路径；能判断行号就填 line，否则填 null
- query 填你为核实该嫌疑建议的检索关键词

只输出 JSON 对象，不要附加任何解释，格式：
{{"suspicions": [{{"file": "...", "line": null, "technology": "fastapi",
"topic": "api 或 security 或 robustness", "description": "嫌疑描述",
"severity_guess": "high 或 medium 或 low", "query": "检索关键词"}}]}}

已确认技术版本：
{versions}

项目代码：
{code}
"""

MIGRATION_SCAN_PROMPT = """\
你是一名版本迁移侦察员。通读以下项目代码，枚举所有使用迁移技术的用法位置。

要求：
- 只枚举"哪里用了、怎么用的"，不做迁移判断（后续逐条检索变更证据核实）
- technology 只能填以下迁移技术之一：{targets}
- 与迁移技术无关的代码不要列；宁可多列，不要漏掉
- file 使用代码中标注的相对路径；能判断行号就填 line，否则填 null
- query 填为核实该用法建议的检索关键词

只输出 JSON 对象，不要附加任何解释，格式：
{{"usage_points": [{{"file": "...", "line": null, "technology": "...",
"usage": "用法描述", "query": "检索关键词"}}]}}

迁移目标（当前 -> 目标）：
{targets_block}

项目代码：
{code}
"""

# ===== 阶段 2 提示词：逐条核实 =====

REVIEW_VERIFY_PROMPT = """\
你是一名审查核实员。针对给定的一条问题嫌疑，结合相关代码与检索证据，\
判断它是否真实成立。

判断规则：
- 检索证据以 [来源: ...] 开头，来自与代码版本一致的官方文档或安全规范：\
证实嫌疑时，evidence 直接引用证据原文，source 填该来源路径
- 检索证据与嫌疑无关或未提供证据：基于自身知识判断，source 设为 "llm_inference"，\
confidence 设为 low 或 medium，evidence 留空
- 有已确认版本时，审查基准是该版本，不得引用其他版本的规范
- 嫌疑不成立或纯属凑数：confirmed 设为 false，其余字段留空即可
- 只报告真实存在的问题，不凑数

只输出 JSON 对象，不要附加任何解释，格式：
{{"confirmed": true, "file": "...", "line": null,
"category": "api 或 security 或 robustness", "severity": "high 或 medium 或 low",
"confidence": "high 或 medium 或 low", "title": "...", "description": "...",
"evidence": "...", "source": "...", "suggestion": "..."}}
"""

MIGRATION_VERIFY_PROMPT = """\
你是一名迁移核实员。针对给定的一条代码用法，结合迁移区间内各版本的 What's New \
变更证据与目标版本规范，判断该用法是否需要迁移调整。

判断规则：
- 多个版本的变更描述同一用法时，按版本号从小到大串联理解演进链，\
最终行为以目标版本文档为准
- 变更文档的文件名常含版本号（如 whatsnew_3.11）：只采信迁移区间\
（当前版本, 目标版本] 内的版本；更早版本的属于历史存档，不得作为迁移依据
- 检索证据以 [来源: ...] 开头：证实需要迁移时，evidence 引用证据原文，\
source 填该来源路径
- 无证据或证据无关：基于自身知识判断，source 设为 "llm_inference"，\
severity 不高于 medium，evidence 留空
- 该用法不受版本变更影响：affected 设为 false，其余字段留空即可

只输出 JSON 对象，不要附加任何解释，格式：
{{"affected": true, "file": "...", "line": null, "technology": "...",
"title": "...", "severity": "high 或 medium 或 low", "current_behavior": "...",
"target_behavior": "...", "reason": "...", "evidence": "...",
"source": "...", "suggested_change": "..."}}
"""


# ===== 阶段 1：清单生成 =====


def _scan_suspicions(model, state: ReviewState, t0: float) -> list[dict]:
    """审查阶段 1：一次无工具调用通读代码，产出嫌疑清单。

    失败抛异常，由调用方回退到 Agent 路径。
    """
    prompt = REVIEW_SCAN_PROMPT.format(
        versions=(
            "\n".join(f"- {t} {v}" for t, v in state["confirmed_versions"].items())
            or "（未提供任何版本，api 维度全部基于自身知识判断）"
        ),
        code=_code_text(state),
    )
    resp = model.invoke([{"role": "user", "content": prompt}])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _extract_json(text)
    if not data or not isinstance(data.get("suspicions"), list):
        raise ValueError("嫌疑清单 JSON 解析失败")
    valid: list[dict] = []
    for item in data["suspicions"]:
        try:
            s = Suspicion.model_validate(item)
        except Exception:
            continue  # 字段不合法的条目直接丢弃
        # file 必须真实存在于 code_context（防幻觉文件）
        if s.file not in state["code_context"]:
            continue
        # 有已确认版本时，api 嫌疑的技术必须在该列表中（防越白名单检索）
        if (
            s.topic == "api"
            and state["confirmed_versions"]
            and s.technology
            and s.technology not in state["confirmed_versions"]
        ):
            continue
        valid.append(s.model_dump())
    print(
        f"[pipeline] 阶段1(审查)完成: 嫌疑 {len(data['suspicions'])}"
        f" -> 有效 {len(valid)}, t={time.monotonic() - t0:.1f}s",
        flush=True,
    )
    return valid


def _scan_usage_points(model, state: ReviewState, t0: float) -> list[dict]:
    """迁移阶段 1：一次无工具调用通读代码，产出用法点清单。失败抛异常。"""
    targets = state.get("target_versions") or {}
    prompt = MIGRATION_SCAN_PROMPT.format(
        targets=", ".join(targets),
        targets_block="\n".join(
            f"- {t} {state['confirmed_versions'].get(t, '?')} -> {v}"
            for t, v in targets.items()
        ),
        code=_code_text(state),
    )
    resp = model.invoke([{"role": "user", "content": prompt}])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _extract_json(text)
    if not data or not isinstance(data.get("usage_points"), list):
        raise ValueError("用法点清单 JSON 解析失败")
    valid: list[dict] = []
    for item in data["usage_points"]:
        try:
            p = UsagePoint.model_validate(item)
        except Exception:
            continue
        if p.file not in state["code_context"]:
            continue
        if p.technology not in targets:
            continue  # 只验证用户指定的迁移技术
        valid.append(p.model_dump())
    print(
        f"[pipeline] 阶段1(迁移)完成: 用法点 {len(data['usage_points'])}"
        f" -> 有效 {len(valid)}, t={time.monotonic() - t0:.1f}s",
        flush=True,
    )
    return valid


# ===== 阶段 2：逐条验证 =====

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _verify_suspicion(model, state: ReviewState, s: dict) -> dict | None:
    """审查阶段 2 单条：选择性检索 + 单次确认调用。

    检索失败降级为无证据确认（不算条目失败）；确认返回 None 表示排除。
    """
    confirmed = state["confirmed_versions"]
    topic = s.get("topic", "api")
    evidence_text = ""
    try:
        if topic == "security":
            evidence_text = _format_docs(
                retrieval.search_security_docs(s.get("query") or s["description"])
            )
        elif topic == "api" and s.get("technology") in confirmed:
            # 版本传已确认值，与 Agent 工具行为一致；白名单校验由确认值本身保证
            evidence_text = _format_docs(
                retrieval.search_official_docs(
                    s["technology"],
                    confirmed[s["technology"]],
                    s.get("query") or s["description"],
                )
            )
        # robustness / 无版本 / 技术未确认：不做版本敏感检索
    except Exception as e:
        print(f"[pipeline] 检索失败({type(e).__name__})，降级为无证据确认: {e}", flush=True)

    line_info = f" 第 {s['line']} 行" if s.get("line") else ""
    user = (
        f"问题嫌疑：\n- 文件：{s['file']}{line_info}"
        f"\n- 维度：{topic}\n- 描述：{s['description']}\n\n"
        f"相关代码：\n=== 文件：{s['file']} ===\n"
        f"{state['code_context'].get(s['file'], '')}\n\n"
        f"检索证据：\n{evidence_text or '（无检索证据，请基于自身知识判断）'}"
    )
    resp = model.invoke(
        [
            {"role": "system", "content": REVIEW_VERIFY_PROMPT},
            {"role": "user", "content": user},
        ]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _extract_json(text)
    if not data or not data.get("confirmed") or not data.get("title"):
        return None
    category = data.get("category") if data.get("category") in ("api", "security", "robustness") else topic
    severity = data.get("severity") if data.get("severity") in _SEVERITY_ORDER else "medium"
    confidence = (
        data.get("confidence")
        if data.get("confidence") in _SEVERITY_ORDER
        else ("medium" if evidence_text else "low")
    )
    line = data.get("line")
    if not isinstance(line, int):
        line = s.get("line")
    source = data.get("source", "llm_inference")
    evidence = data.get("evidence", "")
    # 代码级校验（Phase 12 补丁）：llm_inference 不得携带证据文字。
    # LLM 偶尔用自身知识写出"证据"却把 source 标为 llm_inference（端到端实证），
    # 统一清空并压低置信度：宁可丢证据，不留不可追溯的"证据"。
    if source == "llm_inference" and evidence:
        evidence = ""
        confidence = "low"
    return {
        "file": data.get("file") or s["file"],
        "line": line,
        "category": category,
        "severity": severity,
        "confidence": confidence,
        "title": data["title"],
        "description": data.get("description", s["description"]),
        "evidence": evidence,
        "source": source,
        "suggestion": data.get("suggestion", ""),
    }


def _verify_usage_point(model, state: ReviewState, p: dict) -> dict | None:
    """迁移阶段 2 单条：区间检索 + 单次确认调用。返回 MigrationIssue dict 或 None。"""
    confirmed = state["confirmed_versions"]
    targets = state.get("target_versions") or {}
    tech = p["technology"]
    evidence_text = ""
    try:
        evidence_text = _format_docs(
            retrieval.search_migration_docs(
                tech, confirmed[tech], targets[tech],
                p.get("query") or p["usage"],
            )
        )
    except Exception as e:
        print(f"[pipeline] 迁移检索失败({type(e).__name__})，降级为无证据确认: {e}", flush=True)

    line_info = f" 第 {p['line']} 行" if p.get("line") else ""
    user = (
        f"用法点：\n- 文件：{p['file']}{line_info}"
        f"\n- 技术：{tech}（{confirmed[tech]} -> {targets[tech]}）"
        f"\n- 用法：{p['usage']}\n\n"
        f"相关代码：\n=== 文件：{p['file']} ===\n"
        f"{state['code_context'].get(p['file'], '')}\n\n"
        f"检索证据（迁移区间 What's New + 目标版本规范）：\n"
        f"{evidence_text or '（无检索证据，请基于自身知识判断）'}"
    )
    resp = model.invoke(
        [
            {"role": "system", "content": MIGRATION_VERIFY_PROMPT},
            {"role": "user", "content": user},
        ]
    )
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _extract_json(text)
    if not data or not data.get("affected") or not data.get("title"):
        return None
    severity = data.get("severity") if data.get("severity") in _SEVERITY_ORDER else "medium"
    # 无证据时 severity 封顶 medium（与迁移证据规则一致）
    if not evidence_text and severity == "high":
        severity = "medium"
    line = data.get("line")
    if not isinstance(line, int):
        line = p.get("line")
    source = data.get("source", "llm_inference")
    evidence = data.get("evidence", "")
    # 代码级校验（Phase 12 补丁）：llm_inference 不得携带证据文字（同审查路径）
    if source == "llm_inference" and evidence:
        evidence = ""
    return {
        "file": data.get("file") or p["file"],
        "line": line,
        "technology": tech,
        "current_version": confirmed[tech],
        "target_version": targets[tech],
        "title": data["title"],
        "severity": severity,
        "current_behavior": data.get("current_behavior", p["usage"]),
        "target_behavior": data.get("target_behavior", ""),
        "reason": data.get("reason", ""),
        "evidence": evidence,
        "source": source,
        "suggested_change": data.get("suggested_change", ""),
    }


# ===== 编排入口 =====


def run_review_pipeline(state: ReviewState, model, t0: float) -> dict:
    """审查模式两阶段管线：清单 -> 按嫌疑度排序 -> 逐条验证。

    阶段 1 失败抛异常（调用方回退 Agent）；单条确认失败只跳过该条。
    """
    suspicions = _scan_suspicions(model, state, t0)
    suspicions.sort(key=lambda s: _SEVERITY_ORDER.get(s.get("severity_guess", "medium"), 1))
    issues: list[dict] = []
    for idx, s in enumerate(suspicions, 1):
        try:
            issue = _verify_suspicion(model, state, s)
        except Exception as e:
            print(
                f"[pipeline] 嫌疑 {idx}/{len(suspicions)} 确认异常，跳过: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            continue
        print(
            f"[pipeline] 嫌疑 {idx}/{len(suspicions)} ({s['file']} {s['topic']})"
            f" -> {'确认' if issue else '排除'}, t={time.monotonic() - t0:.1f}s",
            flush=True,
        )
        if issue:
            issues.append(issue)
    issues = _dedupe_issues(issues)
    summary = (
        f"两阶段管线审查完成：侦察出 {len(suspicions)} 条嫌疑，"
        f"逐条检索核实后确认 {len(issues)} 条。"
    )
    return {"summary": summary, "issues": issues}


def run_migration_code_direction(state: ReviewState, model, t0: float) -> list[dict]:
    """迁移代码方向：用法点清单 -> 逐条区间检索验证。

    返回 MigrationIssue dict 列表（不含 confidence，由 merge_bidirectional 填写）。
    失败抛异常，由调用方兜底（保留文档方向结果）。
    """
    points = _scan_usage_points(model, state, t0)
    issues: list[dict] = []
    for idx, p in enumerate(points, 1):
        try:
            issue = _verify_usage_point(model, state, p)
        except Exception as e:
            print(
                f"[pipeline] 用法点 {idx}/{len(points)} 确认异常，跳过: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            continue
        print(
            f"[pipeline] 用法点 {idx}/{len(points)} ({p['file']} {p['technology']})"
            f" -> {'确认迁移点' if issue else '排除'}, t={time.monotonic() - t0:.1f}s",
            flush=True,
        )
        if issue:
            issues.append(issue)
    return _dedupe_issues(issues)


def merge_bidirectional(doc_issues: list[dict], code_issues: list[dict]) -> list[dict]:
    """迁移双向对照合并。

    匹配键 = (file, technology)：
    - 双侧命中 -> confidence=high（文档变更依据 + 代码用法点互相印证）
    - 仅文档侧 -> medium（变更已报告，代码位置建议人工复核）
    - 仅代码侧 -> low（待商榷：检索未召回变更依据）

    证据互补：代码侧无证据时借文档侧证据。局限（刻意保持简单可解释）：
    同文件同技术的多个不同迁移点可能被合并为一条高置信。
    """
    doc_pool = [dict(d) for d in doc_issues]
    merged: list[dict] = []
    for c in code_issues:
        key = (c.get("file", ""), c.get("technology", ""))
        hit = next(
            (
                i
                for i, d in enumerate(doc_pool)
                if (d.get("file", ""), d.get("technology", "")) == key
            ),
            None,
        )
        c = dict(c)
        if hit is not None:
            d = doc_pool.pop(hit)
            c["confidence"] = "high"
            if not c.get("evidence") and d.get("evidence"):
                c["evidence"] = d["evidence"]
                c["source"] = d.get("source") or c.get("source") or "llm_inference"
        else:
            c["confidence"] = "low"
        merged.append(c)
    for d in doc_pool:
        d["confidence"] = "medium"
        merged.append(d)
    return merged
