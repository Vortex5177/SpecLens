r"""BGE-M3 Embedding 连通性测试（独立于后端服务，验证模型可加载）。

验证：
1. 模型能加载（首次会从 HuggingFace/镜像下载，约 2.3GB）
2. 输出向量维度为 1024（与 Qdrant collection 配置一致）

运行：.\backend\.venv\Scripts\python.exe scripts\test_bge_m3.py
网络慢时可在运行前设置镜像：$env:HF_ENDPOINT = "https://hf-mirror.com"
"""
from langchain_huggingface import HuggingFaceEmbeddings

MODEL = "BAAI/bge-m3"
EXPECTED_DIM = 1024


def main() -> None:
    print(f"加载模型 {MODEL}（首次运行需下载，请耐心等待）...")
    embeddings = HuggingFaceEmbeddings(
        model_name=MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    vector = embeddings.embed_query("FastAPI 依赖注入")
    assert len(vector) == EXPECTED_DIM, f"维度不符：{len(vector)} != {EXPECTED_DIM}"
    print(f"[OK] 模型加载成功，向量维度 = {len(vector)}")

    # 顺带验证语义区分度：同主题应比异主题更相似
    vectors = embeddings.embed_documents(["FastAPI 依赖注入", "依赖注入写法", "今天天气不错"])
    sim_related = sum(a * b for a, b in zip(vectors[0], vectors[1]))
    sim_unrelated = sum(a * b for a, b in zip(vectors[0], vectors[2]))
    print(f"[OK] 相似度对比：同主题={sim_related:.4f} > 异主题={sim_unrelated:.4f}")
    assert sim_related > sim_unrelated
    print("BGE-M3 测试全部通过 [OK]")


if __name__ == "__main__":
    main()
