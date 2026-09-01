"""Code Review 路由（规格第 25 节）。

提供：
- POST /api/reviews             创建并同步执行 Review（V1 不引入任务队列）
- GET  /api/reviews/{review_id} 查询 Review 结果（V1 中 review_id 即 project_id）

结果持久化在 uploads/{project_id}/review.json，重复审查会覆盖旧结果。
"""
import json
import re

from fastapi import APIRouter, HTTPException

from app import config
from app.graph.graph import build_review_graph
from app.models.schemas import ReviewRequest, ReviewResponse, ReviewResult

router = APIRouter()

# 与 project 路由一致：32 位十六进制，防路径注入
_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@router.post("/reviews", response_model=ReviewResponse, status_code=201)
def create_review(request: ReviewRequest) -> ReviewResponse:
    """同步执行完整审查流程：分析 -> Agent 审查 -> 生成 Fix Prompt。"""
    project_id = request.project_id
    if not _ID_PATTERN.match(project_id):
        raise HTTPException(status_code=400, detail="无效的项目 ID")
    project_dir = config.UPLOAD_DIR / project_id
    if not (project_dir / "meta.json").is_file():
        raise HTTPException(status_code=404, detail="项目不存在，请先上传")

    graph = build_review_graph()
    try:
        final_state = graph.invoke(
            {
                "project_id": project_id,
                "project_path": str(project_dir),
                "mode": request.mode,
            }
        )
    except Exception as exc:  # LLM / 向量库异常不向前端泄露堆栈（规格第 27 节）
        raise HTTPException(status_code=500, detail=f"审查执行失败：{exc}") from exc

    if final_state.get("error"):
        raise HTTPException(status_code=400, detail=final_state["error"])

    result = ReviewResult(summary=final_state["summary"], issues=final_state["issues"])
    payload = {
        "review_id": project_id,
        "project_id": project_id,
        "mode": request.mode,
        "result": result.model_dump(),
        "project_fix_prompt": final_state["project_fix_prompt"],
    }
    _save_review(project_dir, payload)
    return ReviewResponse(**{k: payload[k] for k in ("review_id", "project_id", "mode", "result")})


@router.get("/reviews/{review_id}")
def get_review(review_id: str) -> dict:
    """查询已保存的 Review 结果（含项目级 Fix Prompt）。"""
    if not _ID_PATTERN.match(review_id):
        raise HTTPException(status_code=400, detail="无效的 Review ID")
    review_path = config.UPLOAD_DIR / review_id / "review.json"
    if not review_path.is_file():
        raise HTTPException(status_code=404, detail="Review 结果不存在，请先创建审查")
    return json.loads(review_path.read_text(encoding="utf-8"))


def _save_review(project_dir, payload: dict) -> None:
    (project_dir / "review.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
