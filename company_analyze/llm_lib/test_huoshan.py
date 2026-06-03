"""测试火山引擎LLM调用"""

import sys
import os

# 确保可以导入llm_lib模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm_lib import chat, chat_stream, HuoshanClient


def test_non_streaming():
    """测试非流式调用"""
    print("=" * 50)
    print("测试1: 非流式调用")
    print("=" * 50)

    prompt = "用一句话介绍一下Python编程语言"
    print(f"提问: {prompt}\n")

    response = chat(prompt)
    print(f"回答: {response}\n")


def test_streaming():
    """测试流式调用"""
    print("=" * 50)
    print("测试2: 流式调用")
    print("=" * 50)

    prompt = """请为天山铝业生成一份专业的投资研究报告，包括以下内容：
1. 公司基本情况和主营业务
2. 行业地位和竞争优势
3. 近期财务表现和关键指标
4. 未来发展前景和投资建议
5. 主要风险因素

请以专业、客观的语气撰写，提供数据支持。"""
    print(f"提问: {prompt}\n")
    print("回答: ", end="")

    for chunk in chat_stream(prompt):
        print(chunk, end="", flush=True)
    print("\n")


def test_client_class():
    """测试使用客户端类"""
    print("=" * 50)
    print("测试3: 使用HuoshanClient类")
    print("=" * 50)

    client = HuoshanClient()

    # 非流式
    prompt = "1+1等于几？"
    print(f"提问: {prompt}\n")
    response = client.chat(prompt)
    print(f"回答: {response}\n")


def test_custom_system_prompt():
    """测试自定义系统提示词"""
    print("=" * 50)
    print("测试4: 自定义系统提示词")
    print("=" * 50)

    custom_system = "你是一个专业的金融分析师，擅长分析股票市场"
    prompt = "如何分析一只股票的基本面？"
    print(f"系统提示: {custom_system}")
    print(f"提问: {prompt}\n")

    response = chat(prompt, system_prompt=custom_system)
    print(f"回答: {response}\n")


if __name__ == "__main__":
    print("\n开始测试火山引擎LLM调用...\n")

    # 检查环境变量
    if not os.environ.get("ARK_API_KEY"):
        print("错误: 未设置环境变量 ARK_API_KEY")
        print("请先设置: export ARK_API_KEY='your-api-key'")
        sys.exit(1)

    try:
        # 运行所有测试
        # test_non_streaming()
        test_streaming()
        # test_client_class()
        # test_custom_system_prompt()

        print("=" * 50)
        print("所有测试完成!")
        print("=" * 50)

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
