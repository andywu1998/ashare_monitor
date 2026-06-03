# DeepSeek API 调通指南（Chat Completions）

以下步骤以 **DeepSeek Chat Completions API** 为例，配合本目录的脚本 `generate_company_report.py` 使用。

## 1. 获取 API Key

1. 登录 DeepSeek 平台，在 API Keys 页面创建并复制你的 Key。
2. 保护好 Key，不要提交到代码库或日志中。

## 2. 设置环境变量

Mac / Linux (zsh / bash)：

```bash
export DEEPSEEK_API_KEY="你的key"
```

如需指定模型：

```bash
export DEEPSEEK_MODEL="deepseek-reasoner"
```

## 3. 快速连通测试（可选）

使用 `curl` 直接调用 Chat Completions API：

```bash
curl https://api.deepseek.com/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" \
  -d '{
    "model": "deepseek-chat",
    "messages": [
      {"role": "user", "content": "Say hello in Chinese."}
    ]
  }'
```

## 4. 运行研报脚本

在本目录下执行：

```bash
python generate_company_report.py "公司名称"
```

脚本会调用 Responses API 生成研报，并将结果保存到 `reports/` 目录。

## 5. 常见问题

- **返回为空或格式异常**：确认 API Key 正确、账户权限正常，并检查返回的 `choices[0].message.content`。
- **模型不可用**：将 `DEEPSEEK_MODEL` 改为你账户可用的模型名称。
- **Python 报网络错误，但 curl 正常**：可设置 `DEEPSEEK_USE_CURL=1` 让脚本使用 `curl` 发送请求。

---

参考文档（官方）：
- DeepSeek API 文档（请求、输出结构示例）
- DeepSeek 平台 API Key 页面与安全建议
