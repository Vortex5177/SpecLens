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


def ingest_knowledge() -> dict:
    """执行一次全量入库（官方文档 + 安全规范），返回统计信息。"""
    official_docs = _collect_official_documents()
    security_docs = _collect_security_documents()
    client = get_qdrant_client()
    ensure_collections(client)

    # QdrantVectorStore 封装 Embedding + 批量写入
    from langchain_qdrant import QdrantVectorStore

    def _write(collection: str, documents: list[Document]) -> None:
        if not documents:
            return
        store = QdrantVectorStore(
            client=client,
            collection_name=collection,
            embedding=get_embeddings(),
        )
        store.add_documents(documents, batch_size=16)

    _write(config.QDRANT_COLLECTION, official_docs)
    _write(config.QDRANT_SECURITY_COLLECTION, security_docs)

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
