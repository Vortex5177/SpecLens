"""知识库入库（ingestion）服务。

规格核心设计：目录结构即版本元数据来源。
knowledge/official/{technology}/{version}/... 中的前两级目录名
直接作为向量的 payload（technology、version），检索时据此做硬性过滤。
安全规范放在 knowledge/security/ 平铺目录，固定 technology=general、version=latest。

每个块携带规格第 12 节要求的完整元数据：
technology / version / source_type / document_type / topic。

流程：扫描目录 -> 按扩展名过滤 -> 分块 -> Embedding -> 写入 Qdrant。
"""
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
    # technology/version 是官方文档每次检索必用的过滤字段，建索引加速。
    # 注意：langchain-qdrant 把 Document.metadata 嵌套存放在 payload.metadata 下，
    # 因此索引与过滤都要用 metadata.* 路径。
    for field in ("metadata.technology", "metadata.version"):
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
                            "document_type": "official_doc",
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


def _chunk_counts_by_source() -> dict[str, int]:
    """从 Qdrant 统计每个知识文件的已入库分块数（source -> chunks）。

    Qdrant 不可用时返回空 dict（目录清单仍可展示，只是不显示入库状态）。
    """
    counts: dict[str, int] = {}
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
                    with_payload=["metadata.source"],
                    with_vectors=False,
                )
                for point in points:
                    source = (point.payload or {}).get("metadata", {}).get("source")
                    if source:
                        counts[source] = counts.get(source, 0) + 1
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


def catalog_knowledge() -> dict:
    """返回本地已有规范文档清单（官方文档按 技术/版本 分组 + 安全规范平铺）。

    每个文档附带已入库分块数：0 或 null 表示尚未入库/无法统计。
    """
    counts = _chunk_counts_by_source()

    def _doc_entry(file_path: Path) -> dict:
        source = file_path.relative_to(config.KNOWLEDGE_DIR).as_posix()
        return {
            "file": file_path.name,
            "topic": file_path.stem,
            "chunks": counts.get(source),
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
