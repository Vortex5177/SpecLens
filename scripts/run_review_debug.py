r"""直接运行 Review Graph（绕过 HTTP），带调试输出定位卡点。

用法：.\backend\.venv\Scripts\python.exe scripts\run_review_debug.py <project_id>
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

from app import config
from app.graph.graph import build_review_graph

project_id = sys.argv[1]
project_dir = config.UPLOAD_DIR / project_id
assert (project_dir / "meta.json").is_file(), "项目不存在"

graph = build_review_graph()
start = time.time()
print("== 开始执行 graph（debug 模式，逐节点输出）==", flush=True)
for step in graph.stream(
    {"project_id": project_id, "project_path": str(project_dir), "mode": "code_review"},
    stream_mode="updates",
):
    for node, update in step.items():
        keys = list(update.keys())
        print(f"[{time.time() - start:6.1f}s] 节点 {node} 完成，输出字段: {keys}", flush=True)
        if "error" in update:
            print("  error:", update["error"], flush=True)
        if "issues" in update:
            print(f"  issues 数量: {len(update['issues'])}", flush=True)
print(f"== 完成，总耗时 {time.time() - start:.1f}s ==", flush=True)
