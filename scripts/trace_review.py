"""定位 review 节点内 agent.invoke 卡死位置的独立脚本。

绕过 HTTP 层，直接调用 graph.stream(stream_mode="updates") 看每个节点耗时。
"""
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from dotenv import load_dotenv
load_dotenv(BACKEND / ".env")

from app.graph.graph import build_review_graph
from app.models.schemas import ReviewRequest


PROJECT_ID = "e82469e02dfc476eaa62ea3b7304de20"
PROJECT_DIR = ROOT / "uploads" / PROJECT_ID


def main() -> None:
    print(f"[test] 项目目录: {PROJECT_DIR}")
    if not PROJECT_DIR.exists():
        print(f"[test] 项目不存在: {PROJECT_DIR}")
        return

    graph = build_review_graph()
    req = ReviewRequest(project_id=PROJECT_ID, mode="code_review")

    t0 = time.monotonic()
    print(f"[test] t=0 开始 graph.stream", flush=True)

    try:
        for chunk in graph.stream(
            {"request": req, "project_path": str(PROJECT_DIR)},
            stream_mode="updates",
        ):
            elapsed = time.monotonic() - t0
            print(f"[test] t={elapsed:.1f}s 节点完成: {list(chunk.keys())}", flush=True)
            for node_name, node_output in chunk.items():
                if isinstance(node_output, dict):
                    keys = list(node_output.keys())
                    summary = str(node_output)[:200]
                    print(f"[test]   {node_name} keys={keys} preview={summary}", flush=True)
    except KeyboardInterrupt:
        print(f"[test] 中断 t={time.monotonic()-t0:.1f}s", flush=True)
        return
    except Exception as e:
        print(f"[test] 异常 t={time.monotonic()-t0:.1f}s: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        return

    print(f"[test] 完成 t={time.monotonic()-t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
