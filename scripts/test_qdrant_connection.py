r"""Qdrant 本地服务最小连通性测试（Phase 4 前置）。

验证四点：
1. Python 能连接本地 Qdrant 服务（http://localhost:6333，非嵌入式）
2. 能创建 collection
3. 能写入向量（含 technology/version 元数据，模拟后续版本过滤场景）
4. 能进行一次相似度搜索

运行：.\backend\.venv\Scripts\python.exe scripts\test_qdrant_connection.py
"""
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

QDRANT_URL = "http://localhost:6333"
COLLECTION = "connection_test"
DIM = 8


def main() -> None:
    # 1. 连接（能列出 collection 即证明服务可达）
    client = QdrantClient(url=QDRANT_URL)
    existing = client.get_collections().collections
    print(f"[1/4] 连接成功：现有 collection = {[c.name for c in existing]}")

    # 2. 创建 collection（重复运行时先删除，保证脚本幂等）
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=DIM, distance=Distance.COSINE),
    )
    print(f"[2/4] 创建 collection 成功：{COLLECTION}（维度={DIM}, 距离=余弦）")

    # 3. 写入向量（payload 带 technology/version，模拟官方文档入库场景）
    points = [
        PointStruct(
            id=1,
            vector=[0.9, 0.1, 0.2, 0.1, 0.0, 0.1, 0.3, 0.2],
            payload={"technology": "fastapi", "version": "0.115", "text": "依赖注入"},
        ),
        PointStruct(
            id=2,
            vector=[0.1, 0.8, 0.1, 0.2, 0.3, 0.1, 0.0, 0.1],
            payload={"technology": "react", "version": "18.2", "text": "hooks"},
        ),
    ]
    client.upsert(collection_name=COLLECTION, points=points, wait=True)
    print(f"[3/4] 写入向量成功：{len(points)} 条")

    # 4. 相似度搜索（查询向量接近第 1 个点，应命中 fastapi）
    results = client.query_points(
        collection_name=COLLECTION,
        query=[0.85, 0.15, 0.2, 0.1, 0.05, 0.1, 0.25, 0.2],
        limit=1,
        with_payload=True,
    ).points
    top = results[0]
    print(f"[4/4] 相似度搜索成功：命中 id={top.id}, 得分={top.score:.4f}, payload={top.payload}")

    # 清理测试 collection，避免污染真实数据
    client.delete_collection(COLLECTION)
    print("测试 collection 已清理。全部通过 [OK]")


if __name__ == "__main__":
    main()
