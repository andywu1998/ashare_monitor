from openai import OpenAI
import os

# 从环境变量中获取您的API KEY，配置方法见：https://www.volcengine.com/docs/82379/1399008
api_key = os.getenv('ARK_API_KEY')
if not api_key:
    raise RuntimeError("ARK_API_KEY is required")

client = OpenAI(
    base_url='https://ark.cn-beijing.volces.com/api/v3',
    api_key=api_key
)

tools = [{
    "type": "web_search",
    "max_keyword": 2,
}]

# 创建一个对话请求
response = client.responses.create(
    model="deepseek-v3-2-251201",
    input=[{"role": "user", "content": "北京的天气怎么样？"}],
    tools=tools,
)

print(response)
