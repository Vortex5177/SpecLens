"""知识检索层（V2）。

- search_official_docs：Official Retriever，强制 technology + version 过滤，
  绝不跨版本返回文档（即使语义上更相似的块属于其他版本，也被硬性排除）。
- search_security_docs：Security Retriever，安全规范不按版本过滤（固定
  technology=general、version=latest），直接语义检索。
- search_migration_docs：Migration Retriever，检索迁移区间（当前版本, 目标版本]
  内的 What's New 变更文档 + 目标版本 Reference，合并返回。
- list_migration_changes：按分区分页枚举迁移区间 What's New 分块（不做向量
  检索），供文档方向候选发现使用。

V2 语义变化（方案第三节）：
- 检索失败抛 RetrievalError，绝不静默返回空列表——"无命中"与"检索失败"
  必须可区分，后者不得被当成无证据核实的依据。
- normalize_version / version_key 公开，作为版本比较逻辑的唯一实现。
- Qdrant / embedding / qdrant-client 等第三方重依赖延迟导入，保持模块
  可离线导入与测试（不连服务的模块级导入不需要这些包）。
"""
import time
from functools import wraps

from app import config


class RetrievalError(RuntimeError):
    """知识检索失败（连接异常 / 超时等）。调用方不得把它当作无命中处理。"""


def normalize_version(v: str) -> str:
    """版本归一化：剥尾部多余 .0，让 0.120.0 与 0.120 等价。"""
    v = str(v).strip()
    while v.endswith(".0") and v.count(".") >= 2:
        v = v[:-2]
    return v


def version_key(v: str) -> tuple[int, ...] | None:
    """把点分版本号解析为整数元组以便比较（如 3.13 -> (3, 13)），非法返回 None。"""
    parts = str(v).strip().split(".")
    if not parts or not all(seg.isdigit() for seg in parts):
        return None
    return tuple(int(seg) for seg in parts)


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise RetrievalError("检索超出本次运行时限")


def _retry_once():
    """瞬时故障一次重试（1s 后）；仍然失败抛 RetrievalError（不吞错误）。"""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, deadline: float | None = None, **kwargs):
            try:
                return fn(*args, **kwargs)
            except RetrievalError:
                raise
            except Exception as exc:
                _check_deadline(deadline)
                time.sleep(1)
                _check_deadline(deadline)
                try:
                    return fn(*args, **kwargs)
                except Exception as retry_exc:
                    raise RetrievalError(
                        f"{fn.__name__} 重试后仍失败：{type(retry_exc).__name__}: {retry_exc}"
                    ) from exc

        return wrapper

    return decorator


def _get_store(collection: str):
    """构造指向指定 collection 的向量存储（重依赖延迟导入）。"""
    from langchain_qdrant import QdrantVectorStore

    from app.rag.embedding import get_embeddings
    from app.services.ingestion import get_qdrant_client

    return QdrantVectorStore(
        client=get_qdrant_client(),
        collection_name=collection,
        embedding=get_embeddings(),
    )


def _official_filter(technology: str, version: str):
    """官方文档的 technology+version 硬性过滤（qdrant 模型延迟导入）。"""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    return Filter(
        must=[
            # langchain-qdrant 的 payload 布局：metadata 嵌套在 payload.metadata 下
            FieldCondition(key="metadata.technology", match=MatchValue(value=technology)),
            FieldCondition(key="metadata.version", match=MatchValue(value=version)),
        ]
    )


def _to_results(docs_with_scores: list[tuple]) -> list[dict]:
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
            "chunk_index": doc.metadata.get("chunk_index"),
            # 官方文档采集时记录的原始 URL（用户上传文档无此字段，返回空串）
            "source_url": doc.metadata.get("source_url", ""),
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
        query, k=limit, filter=_official_filter(technology, version)
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


def _versions_in_range(technology: str, current: str, target: str) -> list[str]:
    """列举知识库里该技术处于迁移区间（当前版本, 目标版本] 的已有版本目录名。

    区间内的版本才可能有影响迁移的 What's New；无法解析为点分数字的
    版本目录不参与区间判断（避免误纳入）。
    """
    tech_dir = config.KNOWLEDGE_DIR / "official" / technology
    if not tech_dir.is_dir():
        return []
    current_key, target_key = version_key(current), version_key(target)
    if current_key is None or target_key is None:
        return []
    versions = []
    for version_dir in tech_dir.iterdir():
        if not version_dir.is_dir():
            continue
        key = version_key(normalize_version(version_dir.name))
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
    current = normalize_version(current_version)
    target = normalize_version(target_version)
    results: list[dict] = []

    from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

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


def _scroll_whats_new(client, collection: str, technology: str, version: str, batch: int, offset):
    """按 metadata 过滤读取一个分区的 What's New 分块，返回（payload 列表, next_offset）。"""
    from qdrant_client.models import FieldCondition, Filter, MatchValue

    points, next_offset = client.scroll(
        collection_name=collection,
        scroll_filter=Filter(
            must=[
                FieldCondition(key="metadata.technology", match=MatchValue(value=technology)),
                FieldCondition(key="metadata.version", match=MatchValue(value=version)),
                FieldCondition(
                    key="metadata.document_type", match=MatchValue(value="whats_new")
                ),
            ]
        ),
        limit=batch,
        offset=offset,
        with_payload=True,
        with_vectors=False,
    )
    items = []
    for point in points:
        payload = point.payload or {}
        metadata = payload.get("metadata") or {}
        content = payload.get("page_content") or payload.get("content") or ""
        items.append({"content": content, "metadata": metadata})
    return items, next_offset


def list_migration_changes(
    confirmed_versions: dict[str, str],
    target_versions: dict[str, str],
    limit: int | None = None,
    deadline: float | None = None,
) -> dict:
    """按分区轮转分页枚举迁移区间（当前, 目标] 内的 What's New 分块。

    confirmed_versions：有效当前版本 {technology: version}；
    target_versions：目标版本 {technology: version}（键小写）。
    不做向量检索、不产生 score；读取顺序在分区间轮转，保证公平覆盖。

    返回：
        results: [{content, metadata}]（与其他检索结果同构，可直接交给
            make_evidence；metadata 含 source/technology/version/document_type/
            chunk_index/source_url）
        partitions: [{technology, version, read, has_more, empty}]
            empty=True 表示知识库中该技术的迁移区间内没有任何 What's New 版本目录。
    """
    from app.services.ingestion import get_qdrant_client

    if limit is None:
        limit = config.DOC_ENUM_MAX_BLOCKS
    _check_deadline(deadline)
    client = get_qdrant_client()

    # 分区：目标技术 x 区间内版本；区间为空的技术记录 empty 分区
    partitions = []
    for tech, target in sorted(target_versions.items()):
        current = confirmed_versions.get(tech)
        range_versions = (
            _versions_in_range(tech, normalize_version(current), normalize_version(target))
            if current
            else []
        )
        if range_versions:
            for version in range_versions:
                partitions.append({"technology": tech, "version": version})
        else:
            partitions.append(
                {"technology": tech, "version": target, "read": 0, "has_more": False, "empty": True}
            )

    state = {
        (p["technology"], p["version"]): {"offset": None, "read": 0, "has_more": True}
        for p in partitions
        if not p.get("empty")
    }
    results: list[dict] = []
    batch = 1  # 每轮每个分区读取的分块数（轮转公平覆盖）

    while len(results) < limit and state:
        _check_deadline(deadline)
        progressed = False
        for key in list(state):
            if len(results) >= limit:
                break
            tech, version = key
            info = state[key]
            if not info["has_more"]:
                continue
            try:
                items, next_offset = _scroll_whats_new(
                    client,
                    config.QDRANT_COLLECTION,
                    tech,
                    version,
                    batch,
                    info["offset"],
                )
            except RetrievalError:
                raise
            except Exception as exc:
                raise RetrievalError(
                    f"枚举 What's New 失败（{tech} {version}）：{type(exc).__name__}: {exc}"
                ) from exc
            # 透传 _scroll_whats_new 的嵌套结构 {content, metadata}，
            # 与其他检索结果（_to_results）同构，保证 make_evidence 可直接消费
            results.extend(items)
            info["read"] += len(items)
            info["offset"] = next_offset
            info["has_more"] = next_offset is not None and len(results) < limit
            progressed = progressed or bool(items)
        if not progressed:
            break

    for p in partitions:
        info = state.get((p["technology"], p["version"]))
        if info is not None:
            p["read"] = info["read"]
            p["has_more"] = info["has_more"]
    return {"results": results, "partitions": partitions}
