"""全局配置与安全限制。

规格第 26 节要求：限制上传大小、文件数量，忽略危险/敏感文件。
所有阈值集中在此，便于调整。
"""
import os
from pathlib import Path

# 上传文件根目录（环境变量可覆盖，Docker 中挂载为持久卷）
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", Path(__file__).resolve().parents[2] / "uploads"))

# ===== 上传限制 =====
MAX_ZIP_SIZE = 50 * 1024 * 1024          # 压缩包最大 50MB
MAX_UNCOMPRESSED_SIZE = 200 * 1024 * 1024  # 解压后总大小最大 200MB（防 zip 炸弹）
MAX_FILE_COUNT = 5000                    # 解压后最多文件数
MAX_SINGLE_FILE = 10 * 1024 * 1024       # 单文件最大 10MB

# ===== 忽略规则 =====
# 目录：不提取、不分析
IGNORED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".idea", ".vscode", ".next", "target",
}

# 敏感文件名前缀：不提取（绝不发送给 LLM）
SENSITIVE_FILE_PREFIXES = (".env",)

# 依赖描述文件（规格第 8 节）
DEPENDENCY_FILES = {
    "requirements.txt", "pyproject.toml", "uv.lock", "poetry.lock",
    "setup.py", "setup.cfg", "Pipfile",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "go.mod", "Cargo.toml", "pom.xml", "build.gradle", "Gemfile",
}

# 文件树展示上限（避免超大项目撑爆响应）
MAX_TREE_ENTRIES = 500

# ===== RAG / 知识库（Phase 4~6）=====
# 预置知识库目录（按 technology/version 组织）
KNOWLEDGE_DIR = Path(os.getenv("KNOWLEDGE_DIR", Path(__file__).resolve().parents[2] / "knowledge"))
# Qdrant 服务地址（本地原生运行默认 6333；禁止使用嵌入式 :memory:）
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
# 官方文档 collection（Phase 5 Official Retriever 使用）
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "official_docs")
# 安全规范 collection（Phase 6 Security Retriever 使用，不按版本过滤）
QDRANT_SECURITY_COLLECTION = os.getenv("QDRANT_SECURITY_COLLECTION", "security_docs")
# BGE-M3 向量维度（模型固定 1024 维，不得修改）
BGE_M3_DIM = 1024
# Embedding 模型（本地运行；国内网络可在 .env 中设置 HF_ENDPOINT=https://hf-mirror.com）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
# 可入库的知识库文档扩展名（其余文件跳过）
KNOWLEDGE_EXTENSIONS = {".md", ".txt", ".rst"}
# 分块参数：每块目标字符数与重叠（中文文档按字符计）
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
