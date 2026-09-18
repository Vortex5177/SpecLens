"""LLM 统一入口（Phase 7+）。

规格约束：使用外部 LLM API（OpenAI 兼容接口），不部署本地生成模型。
规范要求：统一使用 init_chat_model 初始化 ChatModel，不直接实例化 provider 类。
模型来源：llm_store（前端“模型设置”页运行时添加/切换）；.env 仅作首次启动种子。
"""
from langchain.chat_models import init_chat_model

from app import llm_store


def get_chat_model(model_id: str | None = None):
    """返回 ChatModel 实例（请求级 model_id 覆盖 > 全局激活模型）。

    所有模型均按 OpenAI 兼容接口接入：model_provider 固定 "openai"，
    通过 base_url 指向 DeepSeek / 本地 vLLM / Ollama 等任意端点。
    构造无网络开销，不做缓存，保证切换后立即生效。
    """
    if model_id:
        record = llm_store.get_model(model_id)
        if record is None:
            raise RuntimeError(
                f"模型不存在：{model_id}（可能已被删除，请在模型设置中重新选择）"
            )
    else:
        record = llm_store.get_active()
        if record is None:
            raise RuntimeError(
                "未配置任何模型：请在前端“模型设置”页添加，或在 backend/.env 中配置 DEEPSEEK_API_KEY"
            )
    return init_chat_model(
        model=record["model"],
        model_provider="openai",
        base_url=record["base_url"],
        api_key=record["api_key"],
        temperature=0,
        # 单轮响应上限 2 分钟，防止挂死
        request_timeout=120,
        max_retries=1,
        # 网关注入标记：审查/迁移请求使用 speclens/scan 硬顶（直连端点忽略此头）
        default_headers={"x-gw-tag": "speclens/scan"},
    )
