"""两个知识检索器（Phase 5 / Phase 6）。

- search_official_docs：Official Retriever，强制 technology + version 过滤，
  绝不跨版本返回文档（即使语义上更相似的块属于其他版本，也被硬性排除）。
- search_security_docs：Security Retriever，安全规范不按版本过滤（固定
  technology=general、version=latest），直接语义检索。
"""
from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client.models import FieldCondition, Filter, MatchValue

from app import config
from app.rag.embedding import get_embeddings
from app.services.ingestion import get_qdrant_client


def _get_store(collection: str) -> QdrantVectorStore:
    """构造指向指定 collection 的向量存储。"""
    return QdrantVectorStore(
        client=get_qdrant_client(),
        collection_name=collection,
        embedding=get_embeddings(),
    )


def _to_results(docs_with_scores: list[tuple[Document, float]]) -> list[dict]:
    """统一检索结果格式：按相似度降序，含内容、得分与来源元数据。"""
    return [
        {
            "score": round(score, 4),
            "content": doc.page_content,
            "source": doc.metadata.get("source", ""),
            "technology": doc.metadata.get("technology", ""),
            "version": doc.metadata.get("version", ""),
            "topic": doc.metadata.get("topic", ""),
        }
        for doc, score in docs_with_scores
    ]


def search_official_docs(
    technology: str, version: str, query: str, limit: int = 5
) -> list[dict]:
    """Official Retriever：按确认的技术版本检索官方文档（规格第 14 节）。

    technology 与 version 必须传入，在向量库层硬性过滤，
    不可能因为语义相似而返回错误版本的文档。
    """
    store = _get_store(config.QDRANT_COLLECTION)
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
    return _to_results(results)


def search_security_docs(query: str, limit: int = 5) -> list[dict]:
    """Security Retriever：语义检索安全规范（规格第 15 节）。

    安全规范与技术版本无关，不做版本过滤；
    独立 collection 保证不会混入官方文档内容。
    """
    store = _get_store(config.QDRANT_SECURITY_COLLECTION)
    results = store.similarity_search_with_relevance_scores(query, k=limit)
    return _to_results(results)
