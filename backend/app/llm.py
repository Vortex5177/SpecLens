"""LLM 统一入口（Phase 7+）。

规格约束：使用外部 LLM API（DeepSeek，OpenAI 兼容接口），不部署本地生成模型。
规范要求：统一使用 init_chat_model 初始化 ChatModel，不直接实例化 provider 类。
"""
from functools import lru_cache

from langchain.chat_models import init_chat_model

from app import config


@lru_cache(maxsize=1)
def get_chat_model():
    """返回 DeepSeek ChatModel 单例。

    DeepSeek 兼容 OpenAI 接口：model_provider 用 "openai"，
    通过 base_url 指向 DeepSeek 网关。
    """
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY：请复制 backend/.env.example 为 backend/.env 并填入 API Key"
        )
    return init_chat_model(
        model=config.DEEPSEEK_MODEL,
        model_provider="openai",
        base_url=config.DEEPSEEK_BASE_URL,
        api_key=config.DEEPSEEK_API_KEY,
        temperature=0,
        # DeepSeek 单轮响应上限 2 分钟，防止挂死
        request_timeout=120,
        max_retries=1,
    )
