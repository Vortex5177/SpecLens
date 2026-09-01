r"""review 节点卡死问题的缩小复现。

真实系统提示 + 单文件代码 + 4 个工具，观察 create_agent 是否挂起。
开启 httpx 调试日志，确认请求是否发出、响应状态。
"""
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("httpx").setLevel(logging.DEBUG)

from langchain_core.messages import HumanMessage

from app.graph.nodes.review import SYSTEM_PROMPT
from app.graph.tools import build_tools
from app.llm import get_chat_model
from app.models.schemas import ReviewResult
from langchain.agents import create_agent

project_path = Path(r"c:\Users\29461\Documents\Qoder\2026-09-01\chat-2\uploads\f10fcf87001f42aeaf04db09cdfe218b\project")
confirmed = {"fastapi": "0.120.0", "uvicorn": "0.30.0", "react": "18.2.0"}
file_tree = ["main.py", "app/auth.py", "requirements.txt"]

tools = build_tools(project_path, confirmed, file_tree)
code = (project_path / "app" / "auth.py").read_text(encoding="utf-8")

human_content = (
    "项目语言：{'Python': 2}\n\n"
    "用户已确认的技术版本（审查与检索的唯一版本依据）：\n- fastapi 0.120.0\n\n"
    f"待审查代码：\n=== 文件：app/auth.py ===\n{code}\n\n"
    "请审查以上代码。需要核实 API 用法时调用 search_official_docs"
    "（technology 与 version 必须使用上面已确认的值），"
    "涉及安全问题时调用 search_security_rules，"
    "需要查看其他相关文件时调用 read_file。"
)

agent = create_agent(
    model=get_chat_model(),
    tools=tools,
    system_prompt=SYSTEM_PROMPT,
    response_format=ReviewResult,
)

print("== agent.invoke 开始 ==", flush=True)
start = time.time()
out = agent.invoke({"messages": [HumanMessage(content=human_content)]})
print(f"== 完成，耗时 {time.time() - start:.1f}s ==", flush=True)
print(out["structured_response"])
