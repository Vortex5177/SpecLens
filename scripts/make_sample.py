"""Phase 2 测试脚本：构造样例项目并打包为 zip。

运行：python scripts/make_sample.py
产物：test_sample/demo.zip
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "test_sample" / "demo-project"

FILES = {
    "main.py": 'from fastapi import FastAPI\n\napp = FastAPI()\n',
    "requirements.txt": "fastapi==0.115.0\nuvicorn>=0.30\n",
    "app/auth.py": "def login(user, pwd):\n    return pwd == '123456'\n",
    "frontend/package.json": '{"dependencies": {"react": "^18.2.0"}}\n',
    # 锁文件中的精确版本应覆盖 package.json 的范围声明（^18.2.0）
    "frontend/package-lock.json": (
        '{"lockfileVersion": 3, "packages": {'
        '"": {"name": "demo"},'
        '"node_modules/react": {"version": "18.2.0"}'
        "}}\n"
    ),
    "frontend/src/index.js": "console.log('hi');\n",
    # 以下两个应被解压逻辑跳过
    ".env": "SECRET=should-not-be-extracted\n",
    "node_modules/lib/index.js": "// should be skipped\n",
}

for rel_path, content in FILES.items():
    path = ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

print(f"样例项目已生成：{ROOT}")
