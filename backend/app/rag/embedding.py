"""本地 Embedding 模型管理（规格要求：BGE-M3 本地运行，不用外部 Embedding API）。

采用单例模式：模型约 2.3GB，首次加载耗时较长，整个进程只加载一次。
"""
from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

from app import config


@lru_cache(maxsize=1)
def get_embeddings() -> HuggingFaceEmbeddings:
    """返回进程内唯一的 BGE-M3 Embedding 实例。

    首次调用会从 HuggingFace 下载模型（国内可设 HF_ENDPOINT 镜像），
    之后复用内存中的模型，输出固定 1024 维向量。
    """
    return HuggingFaceEmbeddings(
        model_name=config.EMBEDDING_MODEL,
        # 本机无 GPU 环境配置时默认 CPU；有 GPU 可改 "cuda"
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
