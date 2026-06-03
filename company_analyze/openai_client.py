"""
OpenAI API客户端
使用OpenAI SDK请求GPT模型
"""

from openai import OpenAI


class GPTClient:
    """GPT客户端，支持自定义站点"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com",
        model: str = "gpt-4"
    ):
        """
        初始化GPT客户端

        Args:
            api_key: API密钥
            base_url: API站点地址，默认为OpenAI官方地址
            model: 使用的模型名称
        """
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        self.model = model

    def chat(self, messages: list, **kwargs) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表，格式: [{"role": "user", "content": "..."}]
            **kwargs: 其他参数，如temperature, max_tokens等

        Returns:
            模型返回的文本内容
        """
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            **kwargs
        )
        return response.choices[0].message.content

    def chat_stream(self, messages: list, **kwargs):
        """
        流式聊天请求

        Args:
            messages: 消息列表
            **kwargs: 其他参数

        Yields:
            流式返回的文本片段
        """
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            **kwargs
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# 使用示例
if __name__ == "__main__":
    # 配置站点信息，从环境变量读取
    import os
    API_KEY = os.environ.get("OPENAI_API_KEY")
    BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://magic666.top/v1")  # 默认可选
    MODEL = os.environ.get("OPENAI_MODEL", "gpt-4")

    # 创建客户端
    client = GPTClient(
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL
    )

    # 示例1: 普通聊天
    print("=== 普通聊天 ===")
    messages = [
        {"role": "user", "content": "你好，请介绍一下自己"}
    ]
    response = client.chat(messages)
    print(response)

    # 示例2: 流式聊天
    print("\n=== 流式聊天 ===")
    messages = [
        {"role": "user", "content": "写一首关于春天的诗"}
    ]
    for chunk in client.chat_stream(messages):
        print(chunk, end="", flush=True)
    print()

    # 示例3: 带参数的请求
    print("\n=== 带参数的请求 ===")
    messages = [
        {"role": "system", "content": "你是一个专业的投资顾问"},
        {"role": "user", "content": "如何进行价值投资？"}
    ]
    response = client.chat(
        messages,
        temperature=0.7,
        max_tokens=500
    )
    print(response)
