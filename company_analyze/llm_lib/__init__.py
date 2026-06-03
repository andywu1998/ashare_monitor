"""LLM库 - 封装各种大语言模型的调用接口"""

from .huoshan import chat, chat_stream, HuoshanClient

__all__ = ["chat", "chat_stream", "HuoshanClient"]
