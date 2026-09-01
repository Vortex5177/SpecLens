r"""DeepSeek 工具调用（function calling）连通性测试。

验证：
1. 普通对话可用（已确认）
2. bind_tools 后模型能正确返回 tool_calls（Agent 依赖此能力）
3. create_agent + response_format 结构化输出能跑通

运行：.\backend\.venv\Scripts\python.exe scripts\test_deepseek_tools.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / "backend" / ".env")

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from app.llm import get_chat_model


@tool
def add(a: int, b: int) -> int:
    """Add two numbers"""
    return a + b


class TinyResult(BaseModel):
    answer: int
    note: str


def main() -> None:
    model = get_chat_model()

    print("[1/2] 测试 bind_tools ...")
    start = time.time()
    bound = model.bind_tools([add])
    resp = bound.invoke([HumanMessage(content="use the add tool to compute 2+3")])
    print(f"[OK] tool_calls={resp.tool_calls} elapsed={time.time() - start:.1f}s")
    assert resp.tool_calls, "模型未返回工具调用"

    print("[2/2] 测试 create_agent + response_format ...")
    from langchain.agents import create_agent

    agent = create_agent(model=model, tools=[add], response_format=TinyResult)
    start = time.time()
    out = agent.invoke({"messages": [HumanMessage(content="compute 2+3 with the tool, then answer")]})
    structured = out["structured_response"]
    print(f"[OK] structured={structured} elapsed={time.time() - start:.1f}s")
    print("DeepSeek 工具调用测试全部通过 [OK]")


if __name__ == "__main__":
    main()
