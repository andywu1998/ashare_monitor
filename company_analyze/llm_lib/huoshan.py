import os
from openai import OpenAI
from typing import Iterator, Optional


class HuoshanClient:
    """火山引擎智能体客户端封装"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3/bots",
        model: str = "bot-20260130204017-f2rvb"
    ):
        """
        初始化火山引擎客户端

        Args:
            api_key: API密钥，如果不提供则从环境变量 ARK_API_KEY 读取
            base_url: API基础URL
            model: 模型ID（智能体ID）
        """
        self.api_key = api_key or os.environ.get("ARK_API_KEY")
        self.base_url = base_url
        self.model = model
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )

    def chat(
        self,
        prompt: str,
        system_prompt: str = "你是豆包，是由字节跳动开发的 AI 人工智能助手"
    ) -> str:
        """
        非流式调用

        Args:
            prompt: 用户输入的提示词
            system_prompt: 系统提示词

        Returns:
            str: 模型返回的完整响应
        """
        completion = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        return completion.choices[0].message.content

    def chat_stream(
        self,
        prompt: str,
        system_prompt: str = "你是豆包，是由字节跳动开发的 AI 人工智能助手"
    ) -> Iterator[str]:
        """
        流式调用

        Args:
            prompt: 用户输入的提示词
            system_prompt: 系统提示词

        Yields:
            str: 模型返回的文本片段
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            stream=True,
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# 便捷函数
_default_client = None


def get_default_client() -> HuoshanClient:
    """获取默认客户端实例"""
    global _default_client
    if _default_client is None:
        _default_client = HuoshanClient()
    return _default_client


def chat(prompt: str, system_prompt: str = "你是豆包，是由字节跳动开发的 AI 人工智能助手") -> str:
    """
    非流式调用（便捷函数）

    Args:
        prompt: 用户输入的提示词
        system_prompt: 系统提示词

    Returns:
        str: 模型返回的完整响应

    Example:
        >>> from llm_lib.huoshan import chat
        >>> response = chat("天山铝业今天的股价是多少？")
        >>> print(response)
    """
    client = get_default_client()
    return client.chat(prompt, system_prompt)


def chat_stream(prompt: str, system_prompt: str = "你是豆包，是由字节跳动开发的 AI 人工智能助手") -> Iterator[str]:
    """
    流式调用（便捷函数）

    Args:
        prompt: 用户输入的提示词
        system_prompt: 系统提示词

    Yields:
        str: 模型返回的文本片段

    Example:
        >>> from llm_lib.huoshan import chat_stream
        >>> for chunk in chat_stream("天山铝业今天的股价是多少？"):
        ...     print(chunk, end="")
    """
    client = get_default_client()
    yield from client.chat_stream(prompt, system_prompt)
