"""知识库入库（ingestion）服务。

规格核心设计：目录结构即版本元数据来源。
knowledge/official/{technology}/{version}/... 中的前两级目录名
直接作为向量的 payload（technology、version），检索时据此做硬性过滤。
安全规范放在 knowledge/security/ 平铺目录，固定 technology=general、version=latest。

每个块携带规格第 12 节要求的完整元数据：
technology / version / source_type / document_type / topic。
官方文档的 document_type 区分 reference（规范参考）与 whats_new（版本变更文档，
Migration 检索的主要信息来源，SpecLens §2/§12）。

流程：扫描目录 -> 按扩展名过滤 -> 分块 -> Embedding -> 写入 Qdrant。
"""
import threading
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from app import config
from app.rag.embedding import get_embeddings

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=config.CHUNK_SIZE,
    chunk_overlap=config.CHUNK_OVERLAP,
)


def infer_document_type(stem: str) -> str:
    """按文件名推断官方文档类型：含 whats_new / whatsnew 的视为版本变更文档，其余为规范参考。

    先统一分隔符再去掉下划线比较，同时命中 whats_new / whats-new / whatsnew 等写法。
    全量扫描与上传自动模式共用；用户上传时也可显式指定，不依赖推断。
    """
    normalized = stem.lower().replace("-", "_").replace(" ", "_")
    return "whats_new" if "whatsnew" in normalized.replace("_", "") else "reference"


def get_qdrant_client() -> QdrantClient:
    """创建到本地 Qdrant 服务的客户端（非嵌入式）。"""
    return QdrantClient(url=config.QDRANT_URL)


def ensure_collections(client: QdrantClient) -> None:
    """确保官方文档与安全规范两个 collection 存在（1024 维余弦）。

    官方文档额外为过滤字段建 payload 索引；
    安全规范不按版本过滤，无需索引。
    """
    for collection in (config.QDRANT_COLLECTION, config.QDRANT_SECURITY_COLLECTION):
        if not client.collection_exists(collection):
            client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=config.BGE_M3_DIM, distance=Distance.COSINE),
            )
    # technology/version/document_type 是检索必用的过滤字段，建索引加速。
    # 注意：langchain-qdrant 把 Document.metadata 嵌套存放在 payload.metadata 下，
    # 因此索引与过滤都要用 metadata.* 路径。
    for field in ("metadata.technology", "metadata.version", "metadata.document_type"):
        client.create_payload_index(
            collection_name=config.QDRANT_COLLECTION,
            field_name=field,
            field_schema="keyword",
        )


def _chunk_file(file_path: Path, metadata: dict) -> list[Document]:
    """将单个知识文件分块为带元数据的 Document 列表（确定性 ID 保证幂等）。"""
    text = file_path.read_text(encoding="utf-8", errors="ignore")
    source = file_path.relative_to(config.KNOWLEDGE_DIR).as_posix()
    documents: list[Document] = []
    for index, chunk in enumerate(_splitter.split_text(text)):
        # 确定性 ID：同一文档重复入库会覆盖旧块，保证幂等
        documents.append(
            Document(
                page_content=chunk,
                metadata=metadata | {"source": source, "chunk_index": index},
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{index}")),
            )
        )
    return documents


def _collect_official_documents() -> list[Document]:
    """扫描 knowledge/official/{technology}/{version}/，构造带元数据的分块文档。"""
    official = config.KNOWLEDGE_DIR / "official"
    documents: list[Document] = []
    if not official.is_dir():
        return documents

    for tech_dir in sorted(p for p in official.iterdir() if p.is_dir()):
        for version_dir in sorted(p for p in tech_dir.iterdir() if p.is_dir()):
            for file_path in sorted(version_dir.rglob("*")):
                if file_path.suffix.lower() not in config.KNOWLEDGE_EXTENSIONS:
                    continue
                documents.extend(
                    _chunk_file(
                        file_path,
                        {
                            "technology": tech_dir.name,
                            "version": version_dir.name,
                            "source_type": "official",
                            # 按文件名推断：whats_new 命名的是版本变更文档（SpecLens §12）
                            "document_type": infer_document_type(file_path.stem),
                            # topic 取文件名（不含扩展名），如 dependencies
                            "topic": file_path.stem,
                        },
                    )
                )
    return documents


def _collect_security_documents() -> list[Document]:
    """扫描 knowledge/security/ 平铺目录（规格第 12 节：technology=general、version=latest）。"""
    security = config.KNOWLEDGE_DIR / "security"
    documents: list[Document] = []
    if not security.is_dir():
        return documents

    for file_path in sorted(security.rglob("*")):
        if not file_path.is_file() or file_path.suffix.lower() not in config.KNOWLEDGE_EXTENSIONS:
            continue
        documents.extend(
            _chunk_file(
                file_path,
                {
                    "technology": "general",
                    "version": "latest",
                    "source_type": "security",
                    "document_type": "security_rule",
                    "topic": file_path.stem,
                },
            )
        )
    return documents


def _write_documents(collection: str, documents: list[Document]) -> None:
    """将带向量的 Document 批量写入指定 collection（QdrantVectorStore 封装 Embedding + 写入）。"""
    if not documents:
        return
    from langchain_qdrant import QdrantVectorStore

    client = get_qdrant_client()
    ensure_collections(client)
    store = QdrantVectorStore(
        client=client,
        collection_name=collection,
        embedding=get_embeddings(),
    )
    store.add_documents(documents, batch_size=16)


def ingest_document(file_path: Path, metadata: dict) -> int:
    """增量入库单个知识文件（用户上传文档后即时可检索，规格第 11 节）。

    按 metadata 中的 source_type 选择 collection；确定性 ID 保证重复上传幂等。
    返回写入的分块数。
    """
    documents = _chunk_file(file_path, metadata)
    collection = (
        config.QDRANT_SECURITY_COLLECTION
        if metadata.get("source_type") == "security"
        else config.QDRANT_COLLECTION
    )
    _write_documents(collection, documents)
    return len(documents)


def _chunk_counts_by_source() -> dict[str, dict]:
    """从 Qdrant 统计每个知识文件的已入库分块数与文档类型（source -> 统计）。

    Qdrant 不可用时返回空 dict（目录清单仍可展示，只是不显示入库状态）。
    """
    counts: dict[str, dict] = {}
    try:
        client = get_qdrant_client()
        for collection in (config.QDRANT_COLLECTION, config.QDRANT_SECURITY_COLLECTION):
            if not client.collection_exists(collection):
                continue
            offset = None
            while True:
                points, offset = client.scroll(
                    collection_name=collection,
                    limit=256,
                    offset=offset,
                    with_payload=["metadata.source", "metadata.document_type"],
                    with_vectors=False,
                )
                for point in points:
                    meta = (point.payload or {}).get("metadata", {})
                    source = meta.get("source")
                    if not source:
                        continue
                    info = counts.setdefault(source, {"chunks": 0, "document_type": ""})
                    info["chunks"] += 1
                    info["document_type"] = info["document_type"] or meta.get("document_type", "")
                if offset is None:
                    break
    except Exception as e:
        print(f"[ingestion] Qdrant 统计失败（忽略）：{type(e).__name__}: {e}", flush=True)
    return counts


def delete_document_vectors(source: str, source_type: str) -> int:
    """按 metadata.source 精确删除 Qdrant 中某个知识文件的全部分块，返回删除数。

    与入库时的 source 命名一致（如 official/fastapi/0.120/x.md 或 security/x.md）。
    Qdrant 不可用或 collection 不存在时返回 0（文件已删，向量残留不影响检索正确性）。
    """
    collection = (
        config.QDRANT_COLLECTION if source_type == "official" else config.QDRANT_SECURITY_COLLECTION
    )
    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = get_qdrant_client()
        if not client.collection_exists(collection):
            return 0
        source_filter = Filter(
            must=[FieldCondition(key="metadata.source", match=MatchValue(value=source))]
        )
        removed = client.count(
            collection_name=collection, count_filter=source_filter, exact=True
        ).count
        if removed:
            client.delete(collection_name=collection, points_selector=source_filter)
        return removed
    except Exception as e:
        print(f"[ingestion] 删除向量失败（忽略）：{type(e).__name__}: {e}", flush=True)
        return 0


# catalog 结果缓存：知识库内容仅在文档增删/全量入库时变化，
# 而构建一次需要全量 scroll Qdrant 统计各文档分块数（大知识库下耗时数十秒）。
# 由写操作接口（上传/删除/全量入库）调用 invalidate_catalog_cache() 主动失效。
_catalog_cache: dict | None = None
_catalog_cache_lock = threading.Lock()


def invalidate_catalog_cache() -> None:
    """清除 catalog 缓存：知识库写操作后调用，下次请求重算。"""
    global _catalog_cache
    with _catalog_cache_lock:
        _catalog_cache = None


def catalog_knowledge() -> dict:
    """返回本地已有规范文档清单（官方文档按 技术/版本 分组 + 安全规范平铺）。

    每个文档附带已入库分块数：0 或 null 表示尚未入库/无法统计。
    结果缓存在内存中，写操作后由 invalidate_catalog_cache() 失效。
    """
    global _catalog_cache
    with _catalog_cache_lock:
        if _catalog_cache is not None:
            return _catalog_cache

    catalog = _build_catalog()

    with _catalog_cache_lock:
        _catalog_cache = catalog
    return catalog


def _build_catalog() -> dict:
    """实际构建 catalog：扫描目录 + 全量统计 Qdrant 分块数（无缓存）。"""
    counts = _chunk_counts_by_source()

    def _doc_entry(file_path: Path) -> dict:
        source = file_path.relative_to(config.KNOWLEDGE_DIR).as_posix()
        info = counts.get(source, {})
        return {
            "file": file_path.name,
            "topic": file_path.stem,
            "chunks": info.get("chunks"),
            # 已入库文档的类型（reference/whats_new 等）；未入库时为推断值，便于前端展示
            "document_type": info.get("document_type") or infer_document_type(file_path.stem),
        }

    official_tree = []
    official = config.KNOWLEDGE_DIR / "official"
    if official.is_dir():
        for tech_dir in sorted(p for p in official.iterdir() if p.is_dir()):
            versions = []
            for version_dir in sorted(p for p in tech_dir.iterdir() if p.is_dir()):
                documents = [
                    _doc_entry(f)
                    for f in sorted(version_dir.rglob("*"))
                    if f.is_file() and f.suffix.lower() in config.KNOWLEDGE_EXTENSIONS
                ]
                if documents:
                    versions.append({"version": version_dir.name, "documents": documents})
            if versions:
                official_tree.append({"technology": tech_dir.name, "versions": versions})

    security_documents = []
    security = config.KNOWLEDGE_DIR / "security"
    if security.is_dir():
        security_documents = [
            _doc_entry(f)
            for f in sorted(security.rglob("*"))
            if f.is_file() and f.suffix.lower() in config.KNOWLEDGE_EXTENSIONS
        ]

    return {
        "official": official_tree,
        "security": security_documents,
        "total_files": sum(len(v["documents"]) for t in official_tree for v in t["versions"])
        + len(security_documents),
    }


def ingest_knowledge() -> dict:
    """执行一次全量入库（官方文档 + 安全规范），返回统计信息。"""
    official_docs = _collect_official_documents()
    security_docs = _collect_security_documents()

    _write_documents(config.QDRANT_COLLECTION, official_docs)
    _write_documents(config.QDRANT_SECURITY_COLLECTION, security_docs)

    return {
        "official": {
            "files_ingested": len({d.metadata["source"] for d in official_docs}),
            "chunks_ingested": len(official_docs),
            "collection": config.QDRANT_COLLECTION,
        },
        "security": {
            "files_ingested": len({d.metadata["source"] for d in security_docs}),
            "chunks_ingested": len(security_docs),
            "collection": config.QDRANT_SECURITY_COLLECTION,
        },
    }
