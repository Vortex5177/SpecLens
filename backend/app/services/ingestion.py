"""知识库入库（ingestion）服务。

规格核心设计：目录结构即版本元数据来源。
knowledge/official/{technology}/{version}/... 中的前两级目录名
直接作为向量的 payload（technology、version），检索时据此做硬性过滤。

流程：扫描目录 -> 按扩展名过滤 -> 分块 -> Embedding -> 写入 Qdrant。
"""
import uuid

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


def ensure_collection(client: QdrantClient) -> None:
    """确保 collection 存在（1024 维余弦），并为过滤字段建 payload 索引。"""
    if not client.collection_exists(config.QDRANT_COLLECTION):
        client.create_collection(
            collection_name=config.QDRANT_COLLECTION,
            vectors_config=VectorParams(size=config.BGE_M3_DIM, distance=Distance.COSINE),
        )
    # technology/version 是每次检索必用的过滤字段，建索引加速。
    # 注意：langchain-qdrant 把 Document.metadata 嵌套存放在 payload.metadata 下，
    # 因此索引与过滤都要用 metadata.* 路径。
    for field in ("metadata.technology", "metadata.version"):
        client.create_payload_index(
            collection_name=config.QDRANT_COLLECTION,
            field_name=field,
            field_schema="keyword",
        )


def _collect_documents() -> list[Document]:
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
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                for index, chunk in enumerate(_splitter.split_text(text)):
                    # 确定性 ID：同一文档重复入库会覆盖旧块，保证幂等
                    source = file_path.relative_to(config.KNOWLEDGE_DIR).as_posix()
                    documents.append(
                        Document(
                            page_content=chunk,
                            metadata={
                                "technology": tech_dir.name,
                                "version": version_dir.name,
                                "source": source,
                                "chunk_index": index,
                            },
                            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{index}")),
                        )
                    )
    return documents


def ingest_knowledge() -> dict:
    """执行一次全量入库，返回统计信息。"""
    documents = _collect_documents()
    client = get_qdrant_client()
    ensure_collection(client)

    if documents:
        # QdrantVectorStore 封装 Embedding + 批量写入
        from langchain_qdrant import QdrantVectorStore

        store = QdrantVectorStore(
            client=client,
            collection_name=config.QDRANT_COLLECTION,
            embedding=get_embeddings(),
        )
        store.add_documents(documents, batch_size=16)

    sources = {d.metadata["source"] for d in documents}
    return {
        "files_ingested": len(sources),
        "chunks_ingested": len(documents),
        "collection": config.QDRANT_COLLECTION,
    }
