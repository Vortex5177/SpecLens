"""LLM 模型管理路由（Chatbox 式添加与切换）。

提供：
- GET    /api/llm/models                      模型列表（Key 打码）与当前激活项
- POST   /api/llm/models                      新增模型
- PUT    /api/llm/models/{model_id}           更新模型（api_key 留空表示不修改）
- DELETE /api/llm/models/{model_id}           删除模型
- POST   /api/llm/models/{model_id}/activate  切换为激活模型
- POST   /api/llm/models/{model_id}/test      连通性测试（用该配置发一次最小请求）
- POST   /api/llm/models/probe                探测端点可用模型列表（添加/编辑前选择）

配置持久化在 backend/data/llm_models.json。
"""
import re
import time

import httpx
from fastapi import APIRouter, HTTPException
from langchain.chat_models import init_chat_model
from pydantic import BaseModel

from app import llm_store

router = APIRouter()

# 与其他路由一致：十六进制 id，防注入
_ID_PATTERN = re.compile(r"^[0-9a-f]{8}$")


class ModelCreateRequest(BaseModel):
    """新增模型请求体。"""

    name: str
    base_url: str
    api_key: str
    model: str


class ModelUpdateRequest(BaseModel):
    """更新模型请求体；api_key 留空表示不修改。"""

    name: str
    base_url: str
    api_key: str = ""
    model: str


class ModelProbeRequest(BaseModel):
    """探测可用模型列表请求体（添加/编辑前使用）。

    编辑场景下 api_key 可留空：回退使用 model_id 对应记录已保存的 Key。
    """

    base_url: str
    api_key: str = ""
    model_id: str | None = None


def _check_id(model_id: str) -> None:
    if not _ID_PATTERN.match(model_id):
        raise HTTPException(status_code=400, detail="无效的模型 ID")


@router.get("/llm/models")
def list_models() -> dict:
    """模型列表（Key 打码）与当前激活项。"""
    return llm_store.list_models()


@router.post("/llm/models", status_code=201)
def create_model(request: ModelCreateRequest) -> dict:
    """新增模型；列表中第一条自动激活。"""
    if not all(
        v.strip() for v in (request.name, request.base_url, request.api_key, request.model)
    ):
        raise HTTPException(status_code=400, detail="名称、Base URL、API Key、模型名均不能为空")
    return llm_store.add_model(
        name=request.name.strip(),
        base_url=request.base_url.strip(),
        api_key=request.api_key.strip(),
        model=request.model.strip(),
    )


def _candidate_model_urls(base_url: str) -> list[str]:
    """按 OpenAI 兼容惯例生成候选的模型列表地址。

    已带版本段（如 /v1）时直接拼接；只有主机名时先试 /v1/models，
    再回退 /models（DeepSeek 等对两种路径均兼容）。
    """
    base = base_url.strip().rstrip("/")
    if re.search(r"/v\d+$", base):
        return [f"{base}/models"]
    return [f"{base}/v1/models", f"{base}/models"]


@router.post("/llm/models/probe")
def probe_models(request: ModelProbeRequest) -> dict:
    """探测 OpenAI 兼容端点当前可用的模型名列表。

    无论成功失败都返回 200，由 ok 字段区分（与 test 端点一致）。
    """
    base_url = request.base_url.strip()
    if not base_url:
        return {"ok": False, "models": [], "error": "Base URL 不能为空"}

    api_key = request.api_key.strip()
    if not api_key and request.model_id:
        record = llm_store.get_model(request.model_id)
        if record:
            api_key = record["api_key"]

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    last_error = ""
    for url in _candidate_model_urls(base_url):
        try:
            resp = httpx.get(url, headers=headers, timeout=10)
        except Exception as exc:  # 连接拒绝/超时/证书错误等
            return {"ok": False, "models": [], "error": f"无法连接：{exc}"[:300]}
        if resp.status_code in (401, 403):
            return {"ok": False, "models": [], "error": "认证失败：API Key 无效或无权限"}
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            continue
        try:
            items = resp.json().get("data") or []
            models = sorted(
                {str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")}
            )
        except Exception:
            last_error = "返回内容不是标准模型列表"
            continue
        if models:
            return {"ok": True, "models": models, "error": ""}
        last_error = "端点未返回任何模型"
    return {"ok": False, "models": [], "error": f"探测失败（{last_error}）：请检查 Base URL"}


@router.put("/llm/models/{model_id}")
def update_model(model_id: str, request: ModelUpdateRequest) -> dict:
    """更新模型；api_key 留空表示不修改。"""
    _check_id(model_id)
    if not all(v.strip() for v in (request.name, request.base_url, request.model)):
        raise HTTPException(status_code=400, detail="名称、Base URL、模型名不能为空")
    record = llm_store.update_model(
        model_id=model_id,
        name=request.name.strip(),
        base_url=request.base_url.strip(),
        api_key=request.api_key.strip(),
        model=request.model.strip(),
    )
    if record is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    return record


@router.delete("/llm/models/{model_id}")
def delete_model(model_id: str) -> dict:
    """删除模型；若删的是激活项，则自动激活剩余第一条。"""
    _check_id(model_id)
    if not llm_store.delete_model(model_id):
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"deleted": model_id}


@router.post("/llm/models/{model_id}/activate")
def activate_model(model_id: str) -> dict:
    """切换为全局激活模型（所有分析默认使用）。"""
    _check_id(model_id)
    if not llm_store.set_active(model_id):
        raise HTTPException(status_code=404, detail="模型不存在")
    return {"active_id": model_id}


@router.post("/llm/models/{model_id}/test")
def test_model(model_id: str) -> dict:
    """连通性测试：用该配置发起一次最小调用。

    无论成功失败都返回 200，由 ok 字段区分（前端直接展示结果）。
    """
    _check_id(model_id)
    record = llm_store.get_model(model_id)
    if record is None:
        raise HTTPException(status_code=404, detail="模型不存在")

    started = time.monotonic()
    try:
        model = init_chat_model(
            model=record["model"],
            model_provider="openai",
            base_url=record["base_url"],
            api_key=record["api_key"],
            temperature=0,
            max_tokens=8,
            request_timeout=30,
            max_retries=0,
        )
        reply = model.invoke("ping")
        text = reply.content if isinstance(reply.content, str) else str(reply.content)
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": text[:100],
        }
    except Exception as exc:  # 测试端点：异常转为结果字段，不抛 500
        return {
            "ok": False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}"[:300],
        }
