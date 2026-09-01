"""节点 2：review（Agent 动态决策 + LLM 结构化输出，Phase 8/9）。

职责边界（规格原则 3）：
- Agent 负责"需要哪些上下文"：按需调用四个只读工具检索官方文档/安全规范/相关文件
- LLM 负责理解与判断：产出三个维度的问题列表（Structured Output）
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
from app.llm import get_chat_model
from app.models.schemas import ReviewResult, ReviewIssue


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


def _synthesize_review_result(model, final_state: dict, step_idx: int) -> ReviewResult:
    """Agent 未能生成结构化输出时，用单次 LLM 调用从已有工具结果汇总 ReviewResult。

    收集 messages 中所有有效的工具结果（排除“终止”与“提示无结果”消息），
    拼成上下文，让 LLM 一次性生成 ReviewResult，不再进行工具调用。
    """
    import json as _json
    import re

    # 提取工具返回的证据
    evidences: list[str] = []
    for msg in final_state.get("messages", []):
        if not isinstance(msg, ToolMessage):
            continue
        c = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "[终止]" in c or "[提示] 知识库中没有" in c:
            continue
        if c.strip().startswith("[来源:"):
            evidences.append(c.strip())
    evidence_block = "\n\n".join(evidences[:6]) if evidences else "（无证据）"

    synth_input = (
        "已收集的证据（共 " + str(len(evidences)) + " 条，截断至前 6 条）：\n"
        + evidence_block
        + "\n\n"
        + f"（Agent 已完成 {step_idx} 步但未生成结构化输出，请基于上文 HumanMessage 中的代码"
        "与以上证据生成 JSON 格式的 ReviewResult）"
    )
    try:
        # 单次调用，直接要求返回 JSON
        response = model.invoke([
            {"role": "system", "content": SYNTHESIZE_PROMPT},
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
            return ReviewResult(**data)
    except Exception as e:
        print(f"[review] synthesize 失败 {type(e).__name__}: {e}", flush=True)
    # 最终保底：返回空 ReviewResult
    return ReviewResult(
        summary=f"Agent 中断（{step_idx} 步），未能生成结构化输出",
        issues=[],
    )


SYSTEM_PROMPT = """\
你是一名严谨的 Version-Aware Code Reviewer。你只分析用户已确认版本的项目代码，\
从三个维度发现问题：

1. api（API / 技术合规）：API 是否符合已确认版本的官方用法、是否使用过时 API、\
参数是否正确、是否存在版本差异。
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
- 审查基准是用户已确认的版本，不得引用其他版本的规范。

其他要求：
- 只报告真实存在的问题，没有问题就返回空列表，不要凑数。
- file 填相对路径，能确定行号就填 line。
- summary 用一两句话概括整体审查结论。

输出要求：
- 一旦发现问题或证据充足，立刻停止工具调用，直接输出 ReviewResult，不要继续探索。
- 你的最终目标是生成 ReviewResult，不是不断查资料。
"""


def review(state: ReviewState) -> dict:
    t0 = time.monotonic()
    print(f"[review] 开始 review 节点, t={t0:.1f}", flush=True)
    project_dir = Path(state["project_path"])
    tools = build_tools(
        project_path=project_dir / "project",
        confirmed_versions=state["confirmed_versions"],
        file_tree=state["file_tree"],
    )

    # 组合 LLM 上下文（规格第 17 节）：项目信息 + 依赖版本 + 当前代码
    versions_text = "\n".join(
        f"- {tech} {version}" for tech, version in state["confirmed_versions"].items()
    )
    code_text = "\n\n".join(
        f"=== 文件：{rel_path} ===\n{content}"
        for rel_path, content in state["code_context"].items()
    )
    human_content = (
        f"项目语言：{state['languages']}\n\n"
        f"用户已确认的技术版本（审查与检索的唯一版本依据）：\n{versions_text}\n\n"
        f"待审查代码：\n{code_text}\n\n"
        "请审查以上代码。需要核实 API 用法时调用 search_official_docs"
        "（technology 与 version 必须使用上面已确认的值），"
        "涉及安全问题时调用 search_security_rules，"
        "需要查看其他相关文件时调用 read_file。"
    )

    print(f"[review] 调用 get_chat_model, t={time.monotonic()-t0:.1f}s", flush=True)
    model = get_chat_model()
    print(f"[review] 调用 create_agent, t={time.monotonic()-t0:.1f}s", flush=True)
    agent = create_agent(
        model=model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        response_format=ReviewResult,
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
        # Agent 未完成结构化输出：收集已有工具结果，用单次 LLM 调用生成 ReviewResult
        print("[review] Agent 未生成 structured_response，启用二次 LLM 总结", flush=True)
        result = _synthesize_review_result(model, final_state, step_idx)

    return {
        "summary": result.summary,
        "issues": [issue.model_dump() for issue in result.issues],
    }
