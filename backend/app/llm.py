"""LLM 统一入口（V2）。

规格约束：使用外部 LLM API（DeepSeek，OpenAI 兼容接口），不部署本地生成模型。
规范要求：统一使用 init_chat_model 初始化 ChatModel，不直接实例化 provider 类。

V2 预算约束（方案第五节）：
- SDK 层重试次数为 0：重试只会叠加救援路径，由运行时限与预算统一控制；
- 单次请求超时与输出 token 上限在构造时固定；运行时限由管线的预算器控制。
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
    if not config.DEEPSEEK_API_KEY and not config.LLM_ALLOW_MISSING_KEY:
        raise RuntimeError(
            "未配置 DEEPSEEK_API_KEY：请复制 backend/.env.example 为 backend/.env 并填入 API Key"
        )
    return init_chat_model(
        model=config.DEEPSEEK_MODEL,
        model_provider="openai",
        base_url=config.DEEPSEEK_BASE_URL,
        api_key=config.DEEPSEEK_API_KEY or "sk-missing",
        temperature=0,
        # 单次模型请求超时不超过 120 秒（方案第六节）
        timeout=config.LLM_CALL_TIMEOUT_SECONDS,
        max_retries=config.LLM_MAX_RETRIES,
        max_tokens=config.LLM_MAX_OUTPUT_TOKENS,
    )
