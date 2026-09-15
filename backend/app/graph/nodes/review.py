"""节点 2：review（发现-核实管线的唯一分发入口）。

V2：不再存在 Agent 执行路径与救援/回退链——审查与迁移共用同一套
"程序发现候选 + 程序持有证据 + 单次结构化核实" 管线，只按 mode 分发。
模型构造失败属于环境配置错误，直接记为流程错误（不做降级推理）。
"""
from app.graph.nodes.pipeline import run_migration_pipeline, run_review_pipeline
from app.graph.state import ReviewState
from app.llm import get_chat_model


def review(state: ReviewState) -> dict:
    """按 mode 分发到对应管线，产出完整运行报告。"""
    scope = state["run_scope"]
    try:
        model = get_chat_model()
    except Exception as exc:
        return {"report": {
            "run_id": scope["run_id"],
            "mode": scope["mode"],
            "status": "failed",
            "summary": "模型初始化失败，无法执行分析。",
            "issues": [],
            "coverage": {},
            "counts": {},
            "unresolved": [],
            "errors": [{"stage": "setup", "type": "model_error", "message": str(exc)}],
        }}
    if scope["mode"] == "migration":
        report = run_migration_pipeline(scope, model)
    else:
        report = run_review_pipeline(scope, model)
    return {"report": report}
