"""知识库路由。

提供：
- POST /api/knowledge/ingest   扫描 knowledge/official 并入库 Qdrant
- GET  /api/knowledge/search   按 technology+version 过滤检索（两者均为必填）
"""
from fastapi import APIRouter, HTTPException, Query

from app.services import retrieval
from app.services.ingestion import ingest_knowledge

router = APIRouter(prefix="/api/knowledge")


@router.post("/ingest")
def ingest() -> dict:
    """全量入库（首次加载 BGE-M3 模型可能需要数分钟）。"""
    try:
        return ingest_knowledge()
    except Exception as exc:  # 入库失败不应向前端泄露堆栈
        raise HTTPException(status_code=500, detail=f"知识库入库失败：{exc}") from exc


@router.get("/search")
def search(
    technology: str = Query(min_length=1, description="技术名，如 fastapi"),
    version: str = Query(min_length=1, description="用户已确认的版本，如 0.110"),
    query: str = Query(min_length=1, description="检索问题"),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    """版本敏感检索：缺少 technology 或 version 直接 400，绝不跨版本返回。"""
    results = retrieval.search_docs(technology, version, query, limit)
    return {"technology": technology, "version": version, "results": results}
