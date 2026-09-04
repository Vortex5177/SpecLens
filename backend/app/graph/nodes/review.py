"""节点 2：review（Phase 12：两阶段管线 + Agent 路径双向复用）。

职责边界（规格原则 3）：
- code_review：默认走两阶段管线（pipeline.py）：阶段 1 无工具侦察产出嫌疑清单，
  阶段 2 程序 for 循环逐条"检索 + 单次确认"；阶段 1 失败回退 Agent 路径
- migration：双向对照 = 文档方向（原 Agent 路径，不动）+ 代码方向（管线），
  按 (file, technology) 合并并标注 confidence（high/medium/low）
- Agent 路径（保留）：Agent 负责"需要哪些上下文"：按需调用只读工具；
  LLM 负责理解与判断：产出结构化问题列表（Structured Output）
- 证据规则（规格第 21 节）：依据必须来自检索结果，知识库无证据时
  source 标为 llm_inference，严禁伪造官方文档依据
"""
import time
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.graph.state import ReviewState
from app.graph.tools import build_tools
from app.graph.nodes.pipeline import (
    merge_bidirectional,
    run_migration_code_direction,
    run_review_pipeline,
)
from app.llm import get_chat_model
from app.models.schemas import MigrationResult, ReviewResult


SYNTHESIZE_PROMPT = """\
你是一名代码审查结果汇总器。根据下面给出的：
- 用户已确认的技术版本
- 待审查代码
- 之前工具调用收集的证据（来自官方文档与安全规范，每条以 [来源: ...] 开头）

请输出一个 JSON 对象，格式如下：
{
  "summary": "一两句概括审查结论",
  "issues": [
    {
      "file": "相对路径，若无法确定填空字符串",
      "line": null 或行号整数,
      "category": "api" / "security" / "robustness" 之一,
      "severity": "high" / "medium" / "low",
      "confidence": "high" / "medium" / "low",
      "title": "问题标题",
      "description": "问题描述",
      "evidence": "直接引用工具返回的文档片段，无证据则留空",
      "source": "证据来源路径，或 \"llm_inference\"",
      "suggestion": "修复建议"
    }
  ]
}

只报告真实存在的问题，不要凑数。证据必须来自给定的工具返回，严禁编造。\
只输出 JSON，不要附加任何解释。
"""


MIGRATION_SYNTHESIZE_PROMPT = """\
你是一名版本迁移分析结果汇总器。根据下面给出的：
- 当前版本与目标版本
- 待迁移代码
- 之前工具调用收集的证据（来自官方文档，每条以 [来源: ...] 开头）

请输出一个 JSON 对象，格式如下：
{
  "summary": "一两句概括迁移结论",
  "issues": [
    {
      "file": "相对路径，若无法确定填空字符串",
      "line": null 或行号整数,
      "technology": "发生迁移的技术",
      "current_version": "当前版本",
      "target_version": "目标版本",
      "title": "迁移问题标题",
      "severity": "high" / "medium" / "low",
      "current_behavior": "代码在当前版本下的行为/用法",
      "target_behavior": "目标版本的规范要求或行为变化",
      "reason": "为什么需要迁移",
      "evidence": "直接引用工具返回的文档片段，无证据则留空",
      "source": "证据来源路径，或 \"llm_inference\"",
      "suggested_change": "建议的代码修改"
    }
  ]
}

只报告真实存在的迁移点，不要凑数。证据必须来自给定的工具返回，严禁编造。\
只输出 JSON，不要附加任何解释。
"""


def _collect_evidences(final_state: dict) -> list[str]:
    """提取 messages 中所有有效的检索证据（排除“终止”与“提示无结果”消息）。"""
    evidences: list[str] = []
    for msg in final_state.get("messages", []):
        if not isinstance(msg, ToolMessage):
            continue
        c = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "[终止]" in c or "[提示] 知识库中没有" in c:
            continue
        if c.strip().startswith("[来源:"):
            evidences.append(c.strip())
    return evidences


def _synthesize_result(model, final_state: dict, step_idx: int, synth_prompt: str, result_cls):
    """Agent 未能生成结构化输出时，用单次 LLM 调用从已有工具结果汇总结果。

    Review 与 Migration 共用：传入对应的汇总提示词与结果模型即可。
    """
    import json as _json
    import re

    evidences = _collect_evidences(final_state)
    evidence_block = "\n\n".join(evidences[:6]) if evidences else "（无证据）"
    synth_input = (
        "已收集的证据（共 " + str(len(evidences)) + " 条，截断至前 6 条）：\n"
        + evidence_block
        + "\n\n"
        + f"（Agent 已完成 {step_idx} 步但未生成结构化输出，请基于上文 HumanMessage 中的代码"
        "与以上证据生成 JSON）"
    )
    try:
        # 单次调用，直接要求返回 JSON，不再进行工具调用
        response = model.invoke([
            {"role": "system", "content": synth_prompt},
            *[m for m in final_state.get("messages", []) if isinstance(m, HumanMessage)],
            {"role": "user", "content": synth_input},
        ])
        text = response.content if isinstance(response.content, str) else str(response.content)
        # 提取 ```json ... ``` 块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
        data = _json.loads(text)
        if isinstance(data, dict):
            result = result_cls(**data)
            # 代码级校验（Phase 12 补丁）：llm_inference 不得携带证据文字。
            # 与管线同规则：LLM 偶尔把字段说明当值填进 evidence（端到端实证），
            # 统一清空：宁可丢证据，不留不可追溯的"证据"。
            for issue in result.issues:
                if issue.source == "llm_inference" and issue.evidence:
                    issue.evidence = ""
            return result
    except Exception as e:
        print(f"[review] synthesize 失败 {type(e).__name__}: {e}", flush=True)
    # 最终保底：返回空结果（字段兼容 ReviewResult / MigrationResult）
    return result_cls(summary=f"Agent 中断（{step_idx} 步），未能生成结构化输出", issues=[])


SYSTEM_PROMPT = """\
你是一名严谨的 Version-Aware Code Reviewer。你从三个维度分析项目代码：

1. api（API / 技术合规）：API 是否符合官方用法、是否使用过时 API、参数是否正确。
若用户提供了已确认版本，以该版本规范为准；若未提供版本信息，基于通用最佳实践判断。
2. security（安全）：明显安全漏洞、输入验证、认证授权、敏感信息、密码处理。
3. robustness（健壮性）：异常处理、边界情况、空值、错误处理等明显可靠性问题。
基于代码本身判断即可。

工具调用规则（必须严格遵守，违反视为失败）：
- 每个维度最多调用 2 次检索工具；search_official_docs 和 search_security_rules 内置硬限制，\
超过后会直接返回“终止”提示。
- 一旦看到“[终止]”工具返回，必须立刻停止调用工具，基于已有证据生成 ReviewResult。
- 检索不到就基于 LLM 自身知识判断，将 source 设为 "llm_inference"，不要换关键词反复试。
- 严禁连续 3 次调用同一工具。
- 证据充足后必须停止工具调用，直接生成 ReviewResult。

证据规则（必须严格遵守）：
- API 与安全问题的 evidence 必须引用你通过工具实际检索到的文档内容，\
source 填写该文档的来源路径（如 official/fastapi/0.120/dependencies.md）。
- 如果知识库检索不到足够证据，不得编造官方依据：将 source 设为 "llm_inference"，\
confidence 设为 low 或 medium，evidence 留空。
- 未提供版本信息时，严禁调用 search_official_docs（无法确定版本，调用无效），\
此时 API 维度全部基于自身知识判断，source 一律为 "llm_inference"。
- 有已确认版本时，审查基准是该版本，不得引用其他版本的规范。

其他要求：
- 只报告真实存在的问题，没有问题就返回空列表，不要凑数。
- file 填相对路径，能确定行号就填 line。
- summary 用一两句话概括整体审查结论。

输出要求：
- 一旦发现问题或证据充足，立刻停止工具调用，直接输出 ReviewResult，不要继续探索。
- 你的最终目标是生成 ReviewResult，不是不断查资料。
"""


MIGRATION_SYSTEM_PROMPT = """\
你是一名严谨的 Version Migration Analyzer。你的任务是分析代码从当前版本迁移到\
目标版本需要调整的地方（规格第 19 节）。

分析方法：
1. 逐文件分析代码中与迁移技术相关的用法。
2. 优先调用 search_migration_changes 检索版本变更证据（自动覆盖迁移区间内各版本的
What's New 变更文档与目标版本规范）；证据不足时再用 search_official_docs
补充（version 只允许目标版本）。
3. 对比结果：找出目标版本中废弃、修改或新增的用法，产出迁移问题。
4. 不涉及的代码（与迁移技术无关）不要分析。
5. 版本变化判断必须基于检索到的变更文档证据，不要凭自身记忆臆测版本变化。

工具调用规则（必须严格遵守，违反视为失败）：
- 每个检索工具最多调用 2 次；search_official_docs 与 search_migration_changes 内置硬限制，超过后会直接返回“终止”提示。
- 一旦看到“[终止]”工具返回，必须立刻停止调用工具，基于已有证据生成结果。
- 检索不到就基于 LLM 自身知识判断，将 source 设为 "llm_inference"，不要换关键词反复试。
- 严禁连续 3 次调用同一工具。
- 证据充足后必须停止工具调用，直接生成结果。
- 需要查看其他相关文件时调用 read_file；Migration 一般不需要 search_security_rules。

证据规则（必须严格遵守）：
- target_behavior 必须基于你实际检索到的目标版本或变更文档，source 填写该文档来源路径\
（如 official/fastapi/0.120/dependencies.md）。
- 变更文档的文件名常含具体版本号（如 whatsnew_3.11）：只采信迁移区间（当前版本, 目标版本]\
内版本的变更内容；文件名显示更早版本的属于历史存档，不得作为迁移依据。
- 检索不到目标版本证据时，不得编造官方依据：source 设为 "llm_inference"，\
severity 不高于 medium，evidence 留空。
- 严禁引用目标版本之外的规范（当前版本规范仅用于理解代码现状）。
- 若当前版本与目标版本对某用法无差异，不要产出迁移问题。

输出要求：
- 每个问题必须包含 technology / current_version / target_version 三元组。
- severity：不迁移会导致破坏性变化为 high；需要调整为 medium；建议性优化为 low。
- 没有迁移点就返回空列表，不要凑数。
- 一旦分析完成或证据充足，立刻停止工具调用，直接输出结果。
"""


def _build_human_content(state: ReviewState) -> str:
    """按模式组装 LLM 的第一条用户消息（项目信息 + 版本 + 代码）。"""
    code_text = "\n\n".join(
        f"=== 文件：{rel_path} ===\n{content}"
        for rel_path, content in state["code_context"].items()
    )
    versions_text = "\n".join(
        f"- {tech} {version}" for tech, version in state["confirmed_versions"].items()
    )
    header = f"项目语言：{state['languages']}\n\n"

    if state["mode"] == "migration":
        targets_text = "\n".join(
            f"- {tech} {state['confirmed_versions'][tech]} -> {version}"
            for tech, version in state["target_versions"].items()
        )
        return (
            header
            + f"当前已确认的技术版本：\n{versions_text}\n\n"
            + f"迁移目标（当前 -> 目标）：\n{targets_text}\n\n"
            + f"待迁移代码：\n{code_text}\n\n"
            "请分析以上代码从当前版本迁移到目标版本需要调整的地方。"
            "优先调用 search_migration_changes 检索版本变更证据"
            "（technology 使用上面的技术名，版本区间已自动绑定），"
            "需要时再用 search_official_docs 补充目标版本规范（version 用目标版本）。"
        )

    # code_review：未提供任何版本信息时，明确告知不要调用官方文档检索（降级模式）
    if not state["confirmed_versions"]:
        return (
            header
            + "未提供任何技术版本信息：本次审查不做版本敏感的官方文档检索。\n\n"
            + f"待审查代码：\n{code_text}\n\n"
            "请基于代码本身与安全规范审查：涉及安全问题时调用 search_security_rules，"
            "API 与健壮性维度直接基于自身知识判断（source 一律为 \"llm_inference\"，"
            "confidence 不高于 medium），不要调用 search_official_docs。"
        )

    return (
        header
        + f"用户已确认的技术版本（审查与检索的唯一版本依据）：\n{versions_text}\n\n"
        + f"待审查代码：\n{code_text}\n\n"
        "请审查以上代码。需要核实 API 用法时调用 search_official_docs"
        "（technology 与 version 必须使用上面已确认的值），"
        "涉及安全问题时调用 search_security_rules，"
        "需要查看其他相关文件时调用 read_file。"
    )


def _review_with_agent(state: ReviewState, t0: float) -> dict:
    """原 Agent 路径（Phase 8/9/11 实现，逻辑未动）：动态工具循环 + 结构化输出。

    Phase 12 起的角色：
    - code_review：两阶段管线的回退路径（阶段 1 失败时启用）
    - migration：双向对照的"文档方向"
    """
    print(f"[review] Agent 路径开始（{state['mode']}）, t={t0:.1f}", flush=True)
    is_migration = state["mode"] == "migration"
    target_versions = state.get("target_versions") or {}
    project_dir = Path(state["project_path"])
    tools = build_tools(
        project_path=project_dir / "project",
        confirmed_versions=state["confirmed_versions"],
        file_tree=state["file_tree"],
        target_versions=target_versions if is_migration else None,
    )

    human_content = _build_human_content(state)
    # 按模式选择提示词与结构化输出模型（其余流程完全共用，规格第 19 节）
    if is_migration:
        system_prompt, response_format, synth_prompt = (
            MIGRATION_SYSTEM_PROMPT, MigrationResult, MIGRATION_SYNTHESIZE_PROMPT
        )
    else:
        system_prompt, response_format, synth_prompt = (
            SYSTEM_PROMPT, ReviewResult, SYNTHESIZE_PROMPT
        )

    print(f"[review] 调用 get_chat_model, t={time.monotonic()-t0:.1f}s", flush=True)
    model = get_chat_model()
    print(f"[review] 调用 create_agent, t={time.monotonic()-t0:.1f}s", flush=True)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=system_prompt,
        response_format=response_format,
    )
    print(f"[review] 开始 agent.stream, t={time.monotonic()-t0:.1f}s", flush=True)
    # 用 stream 代替 invoke，打印每一步，便于定位卡死位置
    final_state = None
    step_idx = 0
    terminate_seen = 0
    # 递归深度：允许 25 轮 LLM + 工具交互（3 个维度 × 3 轮 = 9，加上 read_file 等）
    stream_config = {"recursion_limit": 30}
    try:
        for chunk in agent.stream(
            {"messages": [HumanMessage(content=human_content)]},
            stream_mode="values",
            config=stream_config,
        ):
            step_idx += 1
            elapsed = time.monotonic() - t0
            msgs = chunk.get("messages", [])
            last = msgs[-1] if msgs else None
            last_type = type(last).__name__ if last else "?"
            # 显示最后一条消息的关键信息
            detail = ""
            if last is not None:
                if hasattr(last, "tool_calls") and last.tool_calls:
                    detail = f" tools={[tc.get('name', '?') for tc in last.tool_calls]}"
                elif hasattr(last, "content"):
                    c = last.content if isinstance(last.content, str) else str(last.content)
                    detail = f" content={c[:80].replace(chr(10), ' ')!r}"
                    if "[终止]" in c:
                        terminate_seen += 1
            print(f"[review] t={elapsed:.1f}s step#{step_idx} msgs={len(msgs)} last={last_type}{detail}", flush=True)
            final_state = chunk
            # 强制停止条件
            if terminate_seen >= 2:
                print(f"[review] 强制中断: 已见 [终止] {terminate_seen} 次", flush=True)
                break
            if step_idx >= 25:
                print("[review] 强制中断: step 已达上限", flush=True)
                break
    except GraphRecursionError as e:
        print(f"[review] GraphRecursionError: {e}", flush=True)
    except Exception as e:
        print(f"[review] Agent 异常 {type(e).__name__}: {e}", flush=True)
        raise
    print(f"[review] agent.stream 完成, t={time.monotonic()-t0:.1f}s, steps={step_idx}", flush=True)
    if final_state is None:
        raise RuntimeError("Agent 未返回任何步骤")
    result = final_state.get("structured_response")
    if result is None:
        # Agent 未完成结构化输出：收集已有工具结果，用单次 LLM 调用汇总
        print("[review] Agent 未生成 structured_response，启用二次 LLM 总结", flush=True)
        result = _synthesize_result(model, final_state, step_idx, synth_prompt, response_format)

    return {
        "summary": result.summary,
        "issues": [issue.model_dump() for issue in result.issues],
    }


def review(state: ReviewState) -> dict:
    """节点 2 分发器（Phase 12）。

    - code_review：两阶段管线（发现-验证分离）；阶段 1 失败回退 Agent 路径
    - migration：文档方向（Agent，原路径不动）+ 代码方向（管线）双向对照合并
    """
    t0 = time.monotonic()
    print(f"[review] 开始 review 节点（{state['mode']}）, t={t0:.1f}", flush=True)
    model = get_chat_model()

    if state["mode"] == "migration":
        # 文档方向：原 Agent 路径（规格第 19 节实现，保持不动）
        doc_result = _review_with_agent(state, t0)
        # 代码方向：两阶段管线（枚举用法点 -> 逐条区间检索验证）
        try:
            code_issues = run_migration_code_direction(state, model, t0)
        except Exception as e:
            print(
                f"[review] 代码方向失败（文档方向结果保留）: {type(e).__name__}: {e}",
                flush=True,
            )
            code_issues = []
        merged = merge_bidirectional(doc_result["issues"], code_issues)
        high = sum(1 for i in merged if i.get("confidence") == "high")
        summary = (
            f"{doc_result['summary']}（双向对照：文档方向 {len(doc_result['issues'])} 条，"
            f"代码方向 {len(code_issues)} 条，双侧命中 {high} 条）"
        )
        print(
            f"[review] 双向对照完成: 文档 {len(doc_result['issues'])} + 代码 {len(code_issues)}"
            f" -> 合并 {len(merged)} 条（双侧 {high}）, t={time.monotonic() - t0:.1f}s",
            flush=True,
        )
        return {"summary": summary, "issues": merged}

    # code_review：优先两阶段管线（发现-验证分离）
    try:
        return run_review_pipeline(state, model, t0)
    except Exception as e:
        print(
            f"[review] 管线阶段1失败，回退 Agent 路径: {type(e).__name__}: {e}",
            flush=True,
        )
        return _review_with_agent(state, t0)
