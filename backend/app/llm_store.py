"""LLM 模型配置存储（多模型管理）。

运行时可在前端“模型设置”页添加/编辑/删除模型并切换激活项；
配置持久化在 backend/data/llm_models.json（含 API Key，已被 .gitignore 排除）。

设计：单用户本地工具，文件级 JSON + 进程内锁即可，不引入数据库。
"""
import json
import threading
import uuid
from pathlib import Path

from app import config

# 存储文件：backend/data/llm_models.json
STORE_PATH = Path(__file__).resolve().parents[1] / "data" / "llm_models.json"

_lock = threading.Lock()


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _migrate_from_env() -> dict:
    """首次使用（文件不存在）：把 .env 的 DEEPSEEK_* 迁移为一条默认模型记录。

    无 DEEPSEEK_API_KEY 时返回空结构（不写盘，保持“未配置”状态）。
    """
    data: dict = {"active_id": None, "models": []}
    if config.DEEPSEEK_API_KEY:
        model_id = uuid.uuid4().hex[:8]
        data["models"].append(
            {
                "id": model_id,
                "name": "DeepSeek (默认)",
                "base_url": config.DEEPSEEK_BASE_URL,
                "api_key": config.DEEPSEEK_API_KEY,
                "model": config.DEEPSEEK_MODEL,
            }
        )
        data["active_id"] = model_id
        _save(data)
    return data


def _load() -> dict:
    if not STORE_PATH.is_file():
        return _migrate_from_env()
    return json.loads(STORE_PATH.read_text(encoding="utf-8"))


def _mask(api_key: str) -> str:
    """打码 API Key：保留前 6 后 4 位；过短则全部隐藏。"""
    if len(api_key) > 12:
        return api_key[:6] + "****" + api_key[-4:]
    return "****"


def _public(record: dict, active_id: str | None) -> dict:
    """对外视图（不含明文 Key）。"""
    return {
        "id": record["id"],
        "name": record["name"],
        "base_url": record["base_url"],
        "model": record["model"],
        "api_key_masked": _mask(record["api_key"]),
        "is_active": record["id"] == active_id,
    }


# ===== 对外接口 =====

def list_models() -> dict:
    """模型列表（Key 打码）与当前激活项。"""
    with _lock:
        data = _load()
    return {
        "active_id": data["active_id"],
        "models": [_public(m, data["active_id"]) for m in data["models"]],
    }


def get_model(model_id: str) -> dict | None:
    """按 id 返回完整配置（含明文 Key，仅后端内部使用）。"""
    with _lock:
        data = _load()
    for record in data["models"]:
        if record["id"] == model_id:
            return record
    return None


def get_active() -> dict | None:
    """当前激活模型的完整配置；未配置时返回 None。"""
    with _lock:
        data = _load()
    for record in data["models"]:
        if record["id"] == data["active_id"]:
            return record
    return None


def add_model(name: str, base_url: str, api_key: str, model: str) -> dict:
    """新增模型；列表中第一条自动激活。"""
    with _lock:
        data = _load()
        record = {
            "id": uuid.uuid4().hex[:8],
            "name": name,
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
        }
        data["models"].append(record)
        if not data["active_id"]:
            data["active_id"] = record["id"]
        _save(data)
        return _public(record, data["active_id"])


def update_model(
    model_id: str, name: str, base_url: str, api_key: str, model: str
) -> dict | None:
    """更新模型；api_key 为空字符串表示不修改。找不到返回 None。"""
    with _lock:
        data = _load()
        for record in data["models"]:
            if record["id"] == model_id:
                record["name"] = name
                record["base_url"] = base_url
                record["model"] = model
                if api_key:
                    record["api_key"] = api_key
                _save(data)
                return _public(record, data["active_id"])
        return None


def delete_model(model_id: str) -> bool:
    """删除模型；若删的是激活项，则自动激活剩余第一条。"""
    with _lock:
        data = _load()
        remaining = [m for m in data["models"] if m["id"] != model_id]
        if len(remaining) == len(data["models"]):
            return False
        data["models"] = remaining
        if data["active_id"] == model_id:
            data["active_id"] = remaining[0]["id"] if remaining else None
        _save(data)
        return True


def set_active(model_id: str) -> bool:
    """切换全局激活模型。"""
    with _lock:
        data = _load()
        if not any(m["id"] == model_id for m in data["models"]):
            return False
        data["active_id"] = model_id
        _save(data)
        return True
