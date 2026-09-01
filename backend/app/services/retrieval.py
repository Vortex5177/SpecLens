"""版本敏感检索器（规格核心约束）。

原则：检索必须携带 technology + version 过滤，绝不跨版本返回文档。
即使语义上更相似的块属于其他版本，也被 metadata filter 硬性排除。
"""
from langchain_qdrant import QdrantVectorStore
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app import config
from app.rag.embedding import get_embeddings
from app.services.ingestion import get_qdrant_client


def _get_store() -> QdrantVectorStore:
    """构造指向官方文档 collection 的向量存储。"""
    return QdrantVectorStore(
        client=get_qdrant_client(),
        collection_name=config.QDRANT_COLLECTION,
        embedding=get_embeddings(),
    )


def search_docs(technology: str, version: str, query: str, limit: int = 5) -> list[dict]:
    """按确认的技术版本检索官方文档。

    返回按相似度降序的结果列表，每项含 score、内容、来源元数据。
    """
    store = _get_store()
    results = store.similarity_search_with_relevance_scores(
        query,
        k=limit,
        filter=Filter(
            must=[
                # langchain-qdrant 的 payload 布局：metadata 嵌套在 payload.metadata 下
                FieldCondition(key="metadata.technology", match=MatchValue(value=technology)),
                FieldCondition(key="metadata.version", match=MatchValue(value=version)),
            ]
        ),
    )
    return [
        {
            "score": round(score, 4),
            "content": doc.page_content,
            "source": doc.metadata.get("source", ""),
            "technology": doc.metadata.get("technology", ""),
            "version": doc.metadata.get("version", ""),
        }
        for doc, score in results
    ]
