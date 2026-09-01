"""三个知识检索器（Phase 5 / Phase 6，SpecLens §6-§7）。

- search_official_docs：Official Retriever，强制 technology + version 过滤，
  绝不跨版本返回文档（即使语义上更相似的块属于其他版本，也被硬性排除）。
- search_security_docs：Security Retriever，安全规范不按版本过滤（固定
  technology=general、version=latest），直接语义检索。
- search_migration_docs：Migration Retriever，检索迁移区间（当前版本, 目标版本]
  内的 What's New 变更文档 + 目标版本 Reference，合并返回。

异常处理：Qdrant 内存压力下可能断连（"Allocation error: not enough memory"），
触发 qdrant-client 默认重试 → Agent 看到错误又重试 → 死循环。
用一次重试（1s 后）缓解瞬时故障；仍然失败则返回空结果，让 Agent 用 LLM 推理。
"""
import time
from functools import wraps

from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

from app import config
from app.rag.embedding import get_embeddings
from app.services.ingestion import get_qdrant_client


def _retry_once():
    """一次重试（1s 后）。仍然失败则返回空结果（不抛异常）。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception:
                time.sleep(1)
                try:
                    return fn(*args, **kwargs)
                except Exception:
                    return []

        return wrapper

    return decorator


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
            "document_type": doc.metadata.get("document_type", ""),
            "topic": doc.metadata.get("topic", ""),
        }
        for doc, score in docs_with_scores
    ]


@_retry_once()
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


@_retry_once()
def search_security_docs(query: str, limit: int = 5) -> list[dict]:
    """Security Retriever：语义检索安全规范（规格第 15 节）。

    安全规范与技术版本无关，不做版本过滤；
    独立 collection 保证不会混入官方文档内容。
    """
    store = _get_store(config.QDRANT_SECURITY_COLLECTION)
    results = store.similarity_search_with_relevance_scores(query, k=limit)
    return _to_results(results)


def _norm_version(v: str) -> str:
    """版本归一化：剥尾部多余 .0，让 0.120.0 与 0.120 等价（与工具层一致）。"""
    while v.endswith(".0") and v.count(".") >= 2:
        v = v[:-2]
    return v


def _version_key(v: str) -> tuple[int, ...] | None:
    """把点分版本号解析为整数元组以便比较（如 3.13 -> (3, 13)），非法返回 None。"""
    parts = [seg for seg in v.strip().split(".")]
    if not parts or not all(seg.isdigit() for seg in parts):
        return None
    return tuple(int(seg) for seg in parts)


def _versions_in_range(technology: str, current: str, target: str) -> list[str]:
    """列举知识库里该技术处于迁移区间（当前版本, 目标版本] 的已有版本目录名。

    区间内的版本才可能有影响迁移的 What's New；无法解析为点分数字的
    版本目录不参与区间判断（避免误纳入）。
    """
    tech_dir = config.KNOWLEDGE_DIR / "official" / technology
    if not tech_dir.is_dir():
        return []
    current_key, target_key = _version_key(current), _version_key(target)
    if current_key is None or target_key is None:
        return []
    versions = []
    for version_dir in tech_dir.iterdir():
        if not version_dir.is_dir():
            continue
        key = _version_key(_norm_version(version_dir.name))
        if key and current_key < key <= target_key:
            versions.append(version_dir.name)
    return sorted(versions)


@_retry_once()
def search_migration_docs(
    technology: str, current_version: str, target_version: str, query: str, limit: int = 5
) -> list[dict]:
    """Migration Retriever（SpecLens §6/§7）：迁移区间 What's New + 目标版本 Reference。

    1. What's New：版本落在（当前, 目标] 区间且 document_type=whats_new 的块，
       通过向量检索按查询语义筛选，不整篇塞给 LLM。
    2. Target Reference：目标版本下除 whats_new 外的规范文档（含旧数据
       的 official_doc 类型，向后兼容），用于确认目标版本的正确用法。
    两组结果合并按相似度降序返回。
    """
    store = _get_store(config.QDRANT_COLLECTION)
    tech = technology.lower()
    current = _norm_version(current_version)
    target = _norm_version(target_version)
    results: list[dict] = []

    range_versions = _versions_in_range(tech, current, target)
    if range_versions:
        whats_new = store.similarity_search_with_relevance_scores(
            query,
            k=limit,
            filter=Filter(
                must=[
                    FieldCondition(key="metadata.technology", match=MatchValue(value=tech)),
                    FieldCondition(key="metadata.version", match=MatchAny(any=range_versions)),
                    FieldCondition(
                        key="metadata.document_type", match=MatchValue(value="whats_new")
                    ),
                ]
            ),
        )
        results.extend(_to_results(whats_new))

    reference = store.similarity_search_with_relevance_scores(
        query,
        k=limit,
        filter=Filter(
            must=[
                FieldCondition(key="metadata.technology", match=MatchValue(value=tech)),
                FieldCondition(key="metadata.version", match=MatchValue(value=target)),
            ],
            # 排除 What's New：剩下的都是目标版本的规范/参考文档（含旧数据类型）
            must_not=[
                FieldCondition(key="metadata.document_type", match=MatchValue(value="whats_new"))
            ],
        ),
    )
    results.extend(_to_results(reference))
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
