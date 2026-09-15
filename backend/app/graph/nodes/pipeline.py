"""发现-核实管线（V2：程序持有证据与身份，LLM 只输出判断）。

结构（方案第四、六节）：
- 发现（多路）：Review 一次代码初筛；Migration 代码方向初筛 + 文档方向分批定位。
  两路候选合并、精确去重，origin 仅记录来源，不决定置信度。
- 核实（单入口）：Review 与 Migration 共用同一核实器，仅提示词与必填字段不同。
  检索失败 -> 候选记 retrieval_error，不进入无证据核实；
  检索成功但无命中 -> 允许无证据核实（confidence 不得为 high，程序校验）。
- 状态：counts / errors / unresolved 如实记录；任何降级都可见，不做静默修复。

预算（三重硬顶）：调用次数、累计输入字符、运行截止时间；token 仅从模型
usage 记录，不做离线预估。单次核实只带单文件代码 + 本候选证据。
"""
import json
import re
import time

from app import config
from app.models.schemas import Candidate
from app.services import retrieval
from app.services.retrieval import RetrievalError
from app.services.validation import (
    VerificationError,
    build_bundle,
    check_candidate_identity,
    make_evidence,
    parse_verification,
)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class PipelineError(RuntimeError):
    """管线内 LLM 调用失败；type 区分类别（model_error / budget_exhausted / timeout）。"""

    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_


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


def _render_code(code_context: dict[str, str]) -> str:
    """把代码快照渲染为带原始行号的文本（行号仅用于定位，不是源码内容）。"""
    blocks = []
    for rel, content in code_context.items():
        lines = content.splitlines()
        numbered = "\n".join(f"{i:>4} | {line}" for i, line in enumerate(lines, 1))
        blocks.append(f"=== 文件：{rel} ===\n{numbered}")
    return "\n\n".join(blocks)


def _format_evidences(evidences: list) -> str:
    """把证据渲染为带证据 ID 的文本块（LLM 只能引用这些 ID）。"""
    if not evidences:
        return ""
    parts = []
    for e in evidences:
        ver = f" | 版本: {e.version}" if e.version else ""
        parts.append(f"[证据ID: {e.evidence_id} | 来源: {e.source}{ver}]\n{e.content}")
    return "\n---\n".join(parts)


# ===== LLM 调用与预算 =====


class _Budget:
    """运行预算状态：调用次数 / 累计输入字符 / 截止时间 / token 记录。"""

    def __init__(self, budget_cfg: dict):
        self.max_calls = budget_cfg["max_llm_calls"]
        self.deadline = time.monotonic() + budget_cfg["deadline"]
        self.calls = 0
        self.input_chars = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def before_call(self, prompt_chars: int) -> None:
        if self.calls + 1 > self.max_calls:
            raise PipelineError("budget_exhausted", f"LLM 调用次数达到上限 {self.max_calls}")
        if time.monotonic() > self.deadline:
            raise PipelineError("timeout", "本次运行超出时间预算")
        if prompt_chars > config.RUN_MAX_INPUT_CHARS_PER_CALL:
            raise PipelineError(
                "budget_exhausted",
                f"单次输入 {prompt_chars} 字符超过上限 {config.RUN_MAX_INPUT_CHARS_PER_CALL}",
            )
        if self.input_chars + prompt_chars > config.RUN_MAX_TOTAL_INPUT_CHARS:
            raise PipelineError(
                "budget_exhausted",
                f"累计输入将达到上限 {config.RUN_MAX_TOTAL_INPUT_CHARS} 字符，强制收尾",
            )

    def record(self, prompt_chars: int, resp) -> None:
        self.calls += 1
        self.input_chars += prompt_chars
        usage = getattr(resp, "usage_metadata", None) or {}
        self.input_tokens += int(usage.get("input_tokens", 0) or 0)
        self.output_tokens += int(usage.get("output_tokens", 0) or 0)


def _invoke_json(model, budget: _Budget, system: str, user: str) -> dict:
    """单次 LLM 调用（预算内），返回解析后的 JSON dict；失败抛 PipelineError。"""
    budget.before_call(len(system) + len(user))
    try:
        resp = model.invoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        )
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError("model_error", f"{type(exc).__name__}: {exc}") from exc
    budget.record(len(system) + len(user), resp)
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    data = _extract_json(text)
    if data is None:
        raise PipelineError("model_error", "模型输出不是合法 JSON")
    return data


# ===== 发现阶段提示词 =====

REVIEW_SCAN_PROMPT = """\
你是一名代码审查侦察员。通读以下项目代码，从三个维度找出所有值得进一步核实的\
问题嫌疑：api（API 用法是否合规/是否过时）、security（安全漏洞）、\
robustness（健壮性）。

要求：
- 只列嫌疑，不下结论：后续会逐条检索证据核实，宁可多列存疑，不要漏掉
- api 维度的嫌疑只针对下面列出的有效技术；security 维度不受此限制
- 与检索核实无关的纯风格问题（命名、注释、格式）不要列
- 代码按"行号 | 内容"标注了原始行号；file 必须使用标注的相对路径，\
line 填对应行号（判断不了再填 null）
- query 填你为核实该嫌疑建议的检索关键词

只输出 JSON 对象，不要附加任何解释，格式：
{{"suspicions": [{{"file": "...", "line": null, "technology": "fastapi",
"topic": "api 或 security 或 robustness", "description": "嫌疑描述",
"severity_guess": "high 或 medium 或 low", "query": "检索关键词"}}]}}

有效技术版本（api 维度检索依据）：
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
- 代码按"行号 | 内容"标注了原始行号；file 必须使用标注的相对路径，\
line 填对应行号（判断不了再填 null）
- query 填为核实该用法建议的检索关键词

只输出 JSON 对象，不要附加任何解释，格式：
{{"usage_points": [{{"file": "...", "line": null, "technology": "...",
"usage": "用法描述", "query": "检索关键词"}}]}}

迁移目标（当前 -> 目标）：
{targets_block}

项目代码：
{code}
"""

MIGRATION_DOC_SCAN_PROMPT = """\
你是一名版本迁移定位员。下面给出迁移区间内的官方变更文档块（带证据 ID）\
和项目源码（带行号）。

任务：从变更文档出发，找出源码中受这些变更影响的实际用法位置。

要求：
- file 必须是源码中出现的相对路径；代码按"行号 | 内容"标注了原始行号，\
line 填对应行号（判断不了再填 null）
- technology 固定填：{tech}
- seed_evidence_ids 只能使用上面给出的证据 ID，可以引用多条
- 只列有明确代码位置的影响点；代码中没有对应用法的变更不要列
- 没有任何影响点时输出空数组

只输出 JSON 对象，不要附加任何解释，格式：
{{"usage_points": [{{"file": "...", "line": null, "technology": "{tech}",
"usage": "受影响的用法描述", "seed_evidence_ids": ["ev-..."]}}]}}
"""

# ===== 核实阶段提示词（单入口共用，输出契约见 VerificationOutput）=====

REVIEW_VERIFY_PROMPT = """\
你是一名审查核实员。针对给定的一条问题嫌疑，结合相关代码与给定证据，\
判断它是否真实成立。

判断规则：
- 只能引用"可用证据"列表中给出的证据 ID（evidence_ids），不得杜撰 ID，\
也不得自写证据正文或来源
- evidence_status=document_supported：判决依据来自给定证据，evidence_ids 必填
- evidence_status=inferred：基于自身知识判断，evidence_ids 必须留空，\
confidence 只能是 medium 或 low（high 仅属于 document_supported）
- evidence_status=none：无法判断（证据无关且自身知识不足），\
decision 必须是 rejected 或 insufficient
- 判断若依赖快照外的代码或前提（如其他文件的定义、外部配置的真实内容），\
不得使用 document_supported，改用 inferred 或 none
- 有有效版本时，审查基准是该版本，不得引用其他版本的规范
- 嫌疑不成立：decision=rejected
- 确认成立（confirmed）时必须给出 title、reason、severity、confidence、suggestion
- 只报告真实存在的问题，不凑数

只输出 JSON 对象，不要附加任何解释，不要输出格式之外的任何字段。\
特别约束：evidence_status=inferred 时 confidence 填 high 属于违规，整条输出将被拒绝：
{{"decision": "confirmed 或 rejected 或 insufficient",
"evidence_status": "document_supported 或 inferred 或 none",
"evidence_ids": ["ev-..."], "title": "...", "reason": "...",
"severity": "high 或 medium 或 low", "confidence": "high 或 medium 或 low",
"suggestion": "..."}}
"""

MIGRATION_VERIFY_PROMPT = """\
你是一名迁移核实员。针对给定的一条代码用法，结合迁移区间内的变更证据与\
目标版本规范，判断该用法是否需要迁移调整。

判断规则：
- 只能引用"可用证据"列表中给出的证据 ID（evidence_ids），不得杜撰 ID，\
也不得自写证据正文或来源
- 多个版本的变更描述同一用法时，按版本号从小到大串联理解演进链，\
最终行为以目标版本文档为准
- evidence_status=document_supported：判决依据来自给定证据，evidence_ids 必填
- evidence_status=inferred：基于自身知识判断，evidence_ids 必须留空，\
confidence 只能是 medium 或 low（high 仅属于 document_supported）
- evidence_status=none：无法判断（证据无关且自身知识不足），\
decision 必须是 rejected 或 insufficient
- 判断若依赖快照外的代码或前提（如其他文件的定义、外部配置的真实内容），\
不得使用 document_supported，改用 inferred 或 none
- 该用法不受版本变更影响：decision=rejected
- 确认需要迁移（confirmed）时必须给出 title、current_behavior、target_behavior、\
change_reason、severity、confidence、suggested_change

只输出 JSON 对象，不要附加任何解释，不要输出格式之外的任何字段。\
特别约束：evidence_status=inferred 时 confidence 填 high 属于违规，整条输出将被拒绝：
{{"decision": "confirmed 或 rejected 或 insufficient",
"evidence_status": "document_supported 或 inferred 或 none",
"evidence_ids": ["ev-..."], "title": "...", "reason": "...",
"severity": "high 或 medium 或 low", "confidence": "high 或 medium 或 low",
"suggestion": "...", "current_behavior": "...", "target_behavior": "...",
"change_reason": "..."}}
"""


# ===== 发现阶段：候选规范化与合并 =====


def _norm_candidate(raw: dict, kind: str, origin: str, scope: dict) -> Candidate | None:
    """把发现阶段原始条目规范化为 Candidate；非法条目返回 None。

    - file 必须在代码快照内、行号必须落在可见范围（防幻觉与越界）
    - api 维度的 technology 必须在有效版本内（防越白名单检索）
    - Migration 候选的 technology 必须在目标技术内
    """
    snapshot_files = scope["snapshot"]["files"]
    # 先归一化 description（Migration 条目用 usage 字段），再做身份校验
    raw = dict(raw)
    raw["description"] = str(raw.get("description") or raw.get("usage") or "").strip()
    identity_error = check_candidate_identity(raw, snapshot_files)
    if identity_error:
        return None
    confirmed = scope["confirmed_versions"]
    technology = str(raw.get("technology", "") or "").strip().lower()
    topic = raw.get("topic") if kind == "suspicion" else "api"
    if topic not in ("api", "security", "robustness"):
        return None
    if topic == "api" and confirmed and technology and technology not in confirmed:
        return None
    if kind == "usage" and technology not in scope["target_versions"]:
        return None
    try:
        return Candidate(
            candidate_id="",
            kind=kind,
            origin=[origin],
            file=str(raw["file"]).replace("\\", "/").lstrip("/"),
            line=raw.get("line"),
            technology=technology,
            dimension=topic,
            description=str(raw.get("description") or raw.get("usage") or "").strip(),
            severity_guess=(
                raw.get("severity_guess") if raw.get("severity_guess") in _SEVERITY_ORDER else "medium"
            ),
            query_terms=str(raw.get("query", "") or "").strip(),
            symbol=str(raw.get("symbol", "") or "").strip(),
            seed_evidence_ids=[str(e) for e in raw.get("seed_evidence_ids", []) or []],
        )
    except Exception:
        return None


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """精确键去重：(kind, file, line, technology, dimension)。合并 origin 与种子证据。

    去重后按序赋候选 ID（cand-001...），后续核实 / 错误 / 未决记录均引用该 ID。
    """
    merged: dict[tuple, Candidate] = {}
    order: list[tuple] = []
    for c in candidates:
        key = (c.kind, c.file, c.line, c.technology, c.dimension)
        if key in merged:
            base = merged[key]
            base.origin = list(dict.fromkeys(base.origin + c.origin))
            base.seed_evidence_ids = list(
                dict.fromkeys(base.seed_evidence_ids + c.seed_evidence_ids)
            )
            continue
        merged[key] = c
        order.append(key)
    for idx, key in enumerate(order, 1):
        merged[key].candidate_id = f"cand-{idx:03d}"
    return [merged[key] for key in order]


# ===== 发现阶段：三条扫描路径 =====


def _scan_review(model, scope: dict, budget: _Budget) -> dict:
    """Review 代码初筛：一次调用通读代码，产出嫌疑清单。失败抛 PipelineError。"""
    code = _render_code(scope["code_context"])
    versions = (
        "\n".join(f"- {t} {v}" for t, v in scope["confirmed_versions"].items())
        or "（未提供任何版本，api 维度全部基于自身知识判断）"
    )
    prompt = REVIEW_SCAN_PROMPT.format(versions=versions, code=code)
    data = _invoke_json(model, budget, "", prompt)
    raw_list = data.get("suspicions")
    if not isinstance(raw_list, list):
        raise PipelineError("model_error", "嫌疑清单 JSON 结构不符（缺少 suspicions 数组）")
    return _collect_scan(raw_list, "suspicion", "code", scope)


def _scan_usage(model, scope: dict, budget: _Budget) -> dict:
    """Migration 代码方向初筛。失败抛 PipelineError。"""
    code = _render_code(scope["code_context"])
    targets = scope["target_versions"]
    confirmed = scope["confirmed_versions"]
    prompt = MIGRATION_SCAN_PROMPT.format(
        targets=", ".join(targets),
        targets_block="\n".join(
            f"- {t} {confirmed.get(t, '?')} -> {v}" for t, v in targets.items()
        ),
        code=code,
    )
    data = _invoke_json(model, budget, "", prompt)
    raw_list = data.get("usage_points")
    if not isinstance(raw_list, list):
        raise PipelineError("model_error", "用法点清单 JSON 结构不符（缺少 usage_points 数组）")
    return _collect_scan(raw_list, "usage", "code", scope)


def _collect_scan(raw_list: list, kind: str, origin: str, scope: dict) -> dict:
    """扫描结果统一收集：规范化 + 丢弃记录。"""
    candidates, discarded = [], {}
    for raw in raw_list:
        if not isinstance(raw, dict):
            reason = "条目不是对象"
            discarded[reason] = discarded.get(reason, 0) + 1
            continue
        c = _norm_candidate(raw, kind, origin, scope)
        if c is None:
            reason = "身份校验失败（文件不存在/行号越界/技术越白名单）"
            discarded[reason] = discarded.get(reason, 0) + 1
            continue
        candidates.append(c)
    return {"candidates": candidates, "total": len(raw_list), "discarded": discarded}


def _scan_doc_direction(model, scope: dict, budget: _Budget, ev_cache: dict) -> dict:
    """Migration 文档方向：分批把区间 What's New 块交给模型定位代码用法。

    枚举由程序完成（list_migration_changes，分页读取）；检索失败 / 定位调用
    失败都向上抛 PipelineError 或 RetrievalError，由编排层记录，不静默吞掉。
    """
    confirmed = scope["confirmed_versions"]
    targets = scope["target_versions"]
    enum = retrieval.list_migration_changes(
        confirmed,
        targets,
        limit=scope["budget"]["doc_enum_max_blocks"],
        deadline=budget.deadline,
    )
    blocks = enum["results"]
    candidates: list[Candidate] = []
    calls = 0
    for item in blocks:
        ev = make_evidence(item)
        if ev is not None:
            ev_cache[ev.evidence_id] = ev

    batch_size = scope["budget"]["doc_scan_batch_blocks"]
    max_calls = scope["budget"]["doc_scan_max_calls"]
    for start in range(0, len(blocks), batch_size):
        if calls >= max_calls:
            break
        batch = blocks[start : start + batch_size]
        batch_evs = [make_evidence(b) for b in batch]
        batch_evs = [e for e in batch_evs if e is not None]
        if not batch_evs:
            continue
        doc_text = _format_evidences(batch_evs)
        tech = batch_evs[0].technology
        prompt = (
            MIGRATION_DOC_SCAN_PROMPT.format(tech=tech)
            + f"\n变更文档块（共 {len(batch_evs)} 块）：\n{doc_text}\n\n"
            f"项目代码：\n{_render_code(scope['code_context'])}\n"
        )
        data = _invoke_json(model, budget, "", prompt)
        calls += 1
        raw_list = data.get("usage_points")
        if not isinstance(raw_list, list):
            continue  # 结构不符的批次直接丢弃，不计失败（文档方向是增益路径）
        valid_ids = {e.evidence_id for e in batch_evs}
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            seeds = [s for s in (str(e) for e in raw.get("seed_evidence_ids", []) or []) if s in valid_ids]
            raw = dict(raw)
            raw["seed_evidence_ids"] = seeds
            if not seeds:
                continue  # 没有合法种子证据的候选不采纳
            c = _norm_candidate(raw, "usage", "document", scope)
            if c is not None:
                candidates.append(c)
    return {
        "candidates": candidates,
        "blocks": len(blocks),
        "calls": calls,
        "partitions": enum["partitions"],
        "has_more": any(p.get("has_more") for p in enum["partitions"]),
    }


# ===== 核实阶段 =====


def _select_evidences(evidences: list, max_chars: int) -> list:
    """按顺序选取证据（完整分块，不截断单块），总字符不超过上限。"""
    selected, used = [], 0
    for e in evidences:
        if used + len(e.content) > max_chars:
            break
        selected.append(e)
        used += len(e.content)
    return selected


def _build_bundle(candidate: Candidate, scope: dict, ev_cache: dict, deadline: float | None = None) -> tuple:
    """为候选构建证据包。返回 (bundle, retrieval_error | None)。

    检索失败（RetrievalError）不降级为无证据核实：候选将计入 failed。
    无检索需求（robustness / 未确认版本的 api）按 no_hit 处理，允许无证据核实。
    """
    confirmed = scope["confirmed_versions"]
    query = candidate.query_terms or candidate.description
    results: list[dict] = []
    status, note = "no_hit", ""
    try:
        if candidate.kind == "usage":
            tech = candidate.technology
            results = retrieval.search_migration_docs(
                tech,
                confirmed[tech],
                scope["target_versions"][tech],
                query,
                deadline=deadline,
            )
            status = "ok"
        elif candidate.dimension == "security":
            results = retrieval.search_security_docs(query)
            status = "ok"
        elif candidate.dimension == "api" and candidate.technology in confirmed:
            results = retrieval.search_official_docs(
                candidate.technology,
                confirmed[candidate.technology],
                query,
            )
            status = "ok"
        elif candidate.dimension == "robustness":
            note = "robustness 维度不进行版本敏感检索"
        else:
            note = "api 候选缺少有效版本，未做版本敏感检索"
    except RetrievalError as exc:
        return build_bundle(candidate.candidate_id, [], "retrieval_error", str(exc)), exc

    evidences = [e for e in (make_evidence(r) for r in results) if e is not None]
    # 文档方向候选并入种子证据（排重后置于检索结果之前：种子是发现依据）
    if candidate.seed_evidence_ids:
        seeds = [ev_cache[sid] for sid in candidate.seed_evidence_ids if sid in ev_cache]
        seen = {e.evidence_id for e in evidences}
        evidences = seeds + [e for e in evidences if e.evidence_id not in seen]
    if status == "ok" and not evidences:
        status = "no_hit"
        note = note or "检索成功但没有命中"
    evidences = _select_evidences(evidences, config.VERIFY_MAX_EVIDENCE_CHARS)
    return build_bundle(candidate.candidate_id, evidences, status, note), None


def _verify_candidate(model, scope: dict, budget: _Budget, candidate: Candidate, bundle) -> dict:
    """统一核实入口：Review 与 Migration 共用。返回核实记录 dict。

    记录字段：candidate_id / decision / verification / error(type, message)。
    校验失败（VerificationError）或模型失败（PipelineError）都记为该候选失败，
    不做静默修复；retrieval_error 的候选在调用前已被排除。
    """
    record = {"candidate_id": candidate.candidate_id, "decision": None}
    if bundle.retrieval_status == "retrieval_error":
        record["error"] = {"type": "retrieval_error", "message": bundle.retrieval_note}
        return record

    is_migration = candidate.kind == "usage"
    code = _render_code({candidate.file: scope["code_context"][candidate.file]})
    evidence_text = _format_evidences(bundle.evidences) or (
        f"（{bundle.retrieval_status}：{bundle.retrieval_note or '无命中'}）"
        if bundle.retrieval_note or bundle.retrieval_status == "no_hit"
        else "（无可用证据）"
    )
    line_info = f" 第 {candidate.line} 行" if candidate.line else ""
    if is_migration:
        system = MIGRATION_VERIFY_PROMPT
        tech = candidate.technology
        user = (
            f"用法点身份（不得改写）：\n- 文件：{candidate.file}{line_info}"
            f"\n- 技术：{tech}（{scope['confirmed_versions'][tech]} -> "
            f"{scope['target_versions'][tech]}）\n- 用法：{candidate.description}\n\n"
            f"相关代码：\n{code}\n\n可用证据（迁移区间 What's New + 目标版本规范）：\n{evidence_text}"
        )
    else:
        system = REVIEW_VERIFY_PROMPT
        user = (
            f"问题嫌疑身份（不得改写）：\n- 文件：{candidate.file}{line_info}"
            f"\n- 维度：{candidate.dimension}\n- 技术：{candidate.technology or '（无）'}"
            f"\n- 描述：{candidate.description}\n\n"
            f"相关代码：\n{code}\n\n可用证据：\n{evidence_text}"
        )

    try:
        data = _invoke_json(model, budget, system, user)
        raw_text = json.dumps(data, ensure_ascii=False)
        output = parse_verification(
            raw_text, bundle, require_migration_fields=is_migration
        )
    except PipelineError as exc:
        record["error"] = {"type": exc.type, "message": str(exc)}
        return record
    except VerificationError as exc:
        record["error"] = {"type": "invalid_output", "message": str(exc)}
        return record
    record["decision"] = output.decision
    record["verification"] = output
    return record


# ===== Issue 构造（身份来自候选，判断来自核实输出）=====


def _referenced_evidences(output, bundle) -> list:
    if not output.evidence_ids:
        return []
    by_id = {e.evidence_id: e for e in bundle.evidences}
    return [by_id[eid] for eid in output.evidence_ids if eid in by_id]


def _make_review_issue(candidate: Candidate, output, bundle) -> dict:
    evidences = _referenced_evidences(output, bundle)
    primary = evidences[0] if evidences else None
    return {
        "file": candidate.file,
        "line": candidate.line,
        "category": candidate.dimension,
        "severity": output.severity,
        "confidence": output.confidence,
        "title": output.title,
        "description": output.reason,
        "evidence": primary.content if primary else "",
        "source": primary.source if primary else "llm_inference",
        "suggestion": output.suggestion,
        "evidence_status": output.evidence_status,
        "origin": candidate.origin,
        "evidences": [e.model_dump() for e in evidences],
        "candidate_ids": [candidate.candidate_id],
    }


def _make_migration_issue(candidate: Candidate, output, bundle, scope: dict) -> dict:
    evidences = _referenced_evidences(output, bundle)
    primary = evidences[0] if evidences else None
    tech = candidate.technology
    return {
        "file": candidate.file,
        "line": candidate.line,
        "technology": tech,
        "current_version": scope["confirmed_versions"][tech],
        "target_version": scope["target_versions"][tech],
        "title": output.title,
        "severity": output.severity,
        "current_behavior": output.current_behavior,
        "target_behavior": output.target_behavior,
        "reason": output.change_reason,
        "evidence": primary.content if primary else "",
        "source": primary.source if primary else "llm_inference",
        "suggested_change": output.suggestion,
        "confidence": output.confidence,
        "evidence_status": output.evidence_status,
        "origin": candidate.origin,
        "evidences": [e.model_dump() for e in evidences],
        "candidate_ids": [candidate.candidate_id],
    }


def _make_unresolved(candidate: Candidate, note: str, type_: str) -> dict:
    return {
        "candidate_id": candidate.candidate_id,
        "type": type_,
        "file": candidate.file,
        "line": candidate.line,
        "technology": candidate.technology,
        "dimension": candidate.dimension,
        "description": candidate.description,
        "origin": candidate.origin,
        "note": note,
    }


# ===== 编排入口 =====


def _sort_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda c: (_SEVERITY_ORDER.get(c.severity_guess, 1), c.file, c.line or 0),
    )


def _run_discovery_and_verify(scope: dict, model: object, scan_paths: list) -> dict:
    """统一编排：执行各发现路径 -> 合并去重 -> 裁剪调度 -> 逐条核实。

    scan_paths: [(名称, 扫描函数, 是否必需)]。必需路径失败 -> 整体 failed；
    非必需路径失败 -> 记录错误，继续（partial）。
    """
    t0 = time.monotonic()
    budget = _Budget(scope["budget"])
    ev_cache: dict[str, object] = {}
    counts = {
        "candidates_total": 0,
        "scheduled": 0,
        "not_scheduled": 0,
        "discarded": 0,
        "verified": 0,
        "confirmed": 0,
        "rejected": 0,
        "insufficient": 0,
        "failed": 0,
        "issues": 0,
        "unresolved": 0,
    }
    errors: list[dict] = []
    unresolved: list[dict] = []
    issues: list[dict] = []
    coverage = {
        "files": scope["snapshot"]["files"],
        "truncated_files": [
            f for f, s in scope["snapshot"]["files"].items() if s.get("truncated")
        ],
        "omitted_files": scope["snapshot"]["omitted_files"],
        "pending_versions": scope["pending_versions"],
        "scans": {},
    }

    candidates: list[Candidate] = []
    fatal = False
    for name, scan_fn, required in scan_paths:
        try:
            result = scan_fn(model, scope, budget, ev_cache)
        except PipelineError as exc:
            errors.append({"stage": "scan", "path": name, "type": exc.type, "message": str(exc)})
            if required:
                fatal = True
            coverage["scans"][name] = {"status": "failed", "error": exc.type}
            continue
        except RetrievalError as exc:
            errors.append({"stage": "scan", "path": name, "type": "retrieval_error", "message": str(exc)})
            if required:
                fatal = True
            coverage["scans"][name] = {"status": "failed", "error": "retrieval_error"}
            continue
        path_candidates: list[Candidate] = result.pop("candidates")
        candidates.extend(path_candidates)
        coverage["scans"][name] = result

    if fatal:
        counts.update({"llm_calls": budget.calls, "input_chars": budget.input_chars,
                       "input_tokens": budget.input_tokens, "output_tokens": budget.output_tokens})
        return {
            "run_id": scope["run_id"],
            "mode": scope["mode"],
            "status": "failed",
            "summary": "发现阶段失败，未产生任何有效分析（详见 errors）。",
            "issues": [],
            "coverage": coverage,
            "counts": counts,
            "unresolved": [],
            "errors": errors,
        }

    merged = _dedupe_candidates(candidates)
    merged = _sort_candidates(merged)
    discarded_total = sum(
        sum(s.get("discarded", {}).values()) for s in coverage["scans"].values() if isinstance(s, dict)
    )
    counts["discarded"] = discarded_total
    counts["candidates_total"] = len(merged)

    # 裁剪调度：超出预算上限的候选记录 not_scheduled（保序：先到先调度）
    scheduled = merged[: scope["budget"]["max_candidates"]]
    not_scheduled = [
        {"candidate_id": c.candidate_id, "file": c.file, "line": c.line,
         "description": c.description[:120], "origin": c.origin}
        for c in merged[scope["budget"]["max_candidates"] :]
    ]
    counts["scheduled"] = len(scheduled)
    counts["not_scheduled"] = len(not_scheduled)

    is_migration = scope["mode"] == "migration"
    for idx, candidate in enumerate(scheduled, 1):
        bundle, retrieval_exc = _build_bundle(candidate, scope, ev_cache, budget.deadline)
        if retrieval_exc is not None:
            errors.append({
                "stage": "verify", "candidate_id": candidate.candidate_id,
                "file": candidate.file, "type": "retrieval_error",
                "message": str(retrieval_exc),
            })
            counts["failed"] += 1
            continue
        record = _verify_candidate(model, scope, budget, candidate, bundle)
        if record.get("error"):
            errors.append({
                "stage": "verify", "candidate_id": candidate.candidate_id,
                "file": candidate.file, **record["error"],
            })
            counts["failed"] += 1
            print(
                f"[pipeline] 候选 {idx}/{len(scheduled)} ({candidate.file}) "
                f"核实失败[{record['error']['type']}]，已记录，t={time.monotonic() - t0:.1f}s",
                flush=True,
            )
            continue
        counts["verified"] += 1
        decision = record["decision"]
        output = record["verification"]
        if decision == "confirmed":
            counts["confirmed"] += 1
            if is_migration:
                if output.evidence_status == "inferred":
                    # Migration 推断型判断：待人工核实，不进 issues / Fix Prompt
                    unresolved.append(
                        _make_unresolved(
                            candidate,
                            "推断型迁移建议（无文档支持），待人工核实",
                            "inferred_suggestion",
                        )
                    )
                else:
                    issues.append(_make_migration_issue(candidate, output, bundle, scope))
            else:
                issues.append(_make_review_issue(candidate, output, bundle))
        elif decision == "rejected":
            counts["rejected"] += 1
        else:  # insufficient
            counts["insufficient"] += 1
            unresolved.append(
                _make_unresolved(candidate, "证据不足，无法可靠判断", "insufficient")
            )
        print(
            f"[pipeline] 候选 {idx}/{len(scheduled)} ({candidate.file} "
            f"{candidate.dimension or candidate.technology}) -> {decision}，"
            f"t={time.monotonic() - t0:.1f}s",
            flush=True,
        )

    issues.sort(key=lambda i: (_SEVERITY_ORDER.get(i.get("severity", "medium"), 1),))
    counts["issues"] = len(issues)
    counts["unresolved"] = len(unresolved)
    counts.update({
        "llm_calls": budget.calls,
        "input_chars": budget.input_chars,
        "input_tokens": budget.input_tokens,
        "output_tokens": budget.output_tokens,
    })
    coverage["doc_direction"] = coverage["scans"].get("document")

    status = _report_status(counts, errors, not_scheduled, coverage, fatal=False)
    summary = _build_summary(scope, counts, status)
    return {
        "run_id": scope["run_id"],
        "mode": scope["mode"],
        "status": status,
        "summary": summary,
        "issues": issues,
        "coverage": coverage,
        "counts": counts,
        "unresolved": unresolved,
        "errors": errors,
    }


def _report_status(counts: dict, errors: list, not_scheduled: list, coverage: dict, fatal: bool) -> str:
    """complete 仅当：零失败、零未决、零未调度、零丢弃、零截断与覆盖缺口。"""
    if fatal:
        return "failed"
    coverage_gaps = (
        coverage.get("truncated_files")
        or coverage.get("omitted_files")
        or coverage.get("pending_versions")
    )
    doc_info = coverage.get("doc_direction")
    has_more = bool(doc_info and doc_info.get("has_more"))
    if (
        errors
        or counts.get("failed")
        or counts.get("insufficient")
        or counts.get("not_scheduled")
        or counts.get("discarded")
        or coverage_gaps
        or has_more
    ):
        return "partial"
    return "complete"


def _build_summary(scope: dict, counts: dict, status: str) -> str:
    """统计性摘要：只描述过程与结果分布，不宣称超出证据的结论。"""
    mode_label = "迁移分析" if scope["mode"] == "migration" else "代码审查"
    parts = [
        f"{mode_label}{'完成' if status == 'complete' else '已部分完成（存在缺口，详见 coverage/errors/unresolved）'}："
        f"候选 {counts['candidates_total']} 条（丢弃 {counts['discarded']}），"
        f"调度核实 {counts['scheduled']} 条，确认 {counts['confirmed']} 条、"
        f"排除 {counts['rejected']} 条、未决 {counts['insufficient']} 条、失败 {counts['failed']} 条。"
    ]
    if counts["not_scheduled"]:
        parts.append(f"另有 {counts['not_scheduled']} 条候选超出调度上限未核实。")
    if scope["pending_versions"]:
        parts.append(f"待确认版本未参与检索：{', '.join(scope['pending_versions'])}。")
    return "".join(parts)


def run_review_pipeline(scope: dict, model) -> dict:
    """审查模式：代码初筛（必需）-> 统一核实。返回完整运行报告。"""
    return _run_discovery_and_verify(
        scope,
        model,
        [("code", lambda m, s, b, ec: _scan_review(m, s, b), True)],
    )


def run_migration_pipeline(scope: dict, model) -> dict:
    """迁移模式：代码方向 + 文档方向（都非必需）-> 统一核实。返回完整运行报告。"""
    return _run_discovery_and_verify(
        scope,
        model,
        [
            ("code", lambda m, s, b, ec: _scan_usage(m, s, b), False),
            ("document", lambda m, s, b, ec: _scan_doc_direction(m, s, b, ec), False),
        ],
    )
