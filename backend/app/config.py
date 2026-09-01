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
