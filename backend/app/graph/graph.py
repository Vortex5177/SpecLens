"""LangGraph 流程装配（Phase 7，规格第 6 节）。

V1 图保持最简：
START -> analyze_project -> review -> generate_result -> END

职责划分（规格原则 3）：
- LangGraph：确定性的整体流程
- Agent（review 节点内部）：动态选择需要哪些上下文
- analyze_project 发现错误（如版本未确认）时直接跳到 END，
  由调用方把 error 转成业务错误返回前端。
"""
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.analyze import analyze_project
from app.graph.nodes.result import generate_result
from app.graph.nodes.review import review
from app.graph.state import ReviewState


def _route_after_analyze(state: ReviewState) -> str:
    """分析阶段出错（版本未确认等）直接终止，不进入 LLM。"""
    return END if state.get("error") else "review"


def build_review_graph():
    graph = StateGraph(ReviewState)
    graph.add_node("analyze_project", analyze_project)
    graph.add_node("review", review)
    graph.add_node("generate_result", generate_result)

    graph.add_edge(START, "analyze_project")
    graph.add_conditional_edges("analyze_project", _route_after_analyze)
    graph.add_edge("review", "generate_result")
    graph.add_edge("generate_result", END)

    return graph.compile()
