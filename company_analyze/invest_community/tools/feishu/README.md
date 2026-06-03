# Feishu 文档自动创建使用说明

本目录脚本会：
1) 新建飞书文档节点（docx）
2) 将 Markdown 转换为内容块
3) 创建嵌套块
4) （可选）把文档链接插入多维表格

## 目录与脚本

- 脚本：`tools/feishu/create_doc_from_md.py`
- 环境变量：项目根目录 `.env`

## 运行前准备

1. 在项目根目录 `.env` 填好以下变量（用于获取 `tenant_access_token_internal`）：

```
FEISHU_APP_ID=""
FEISHU_APP_SECRET=""
FEISHU_SPACE_ID=""
FEISHU_PARENT_NODE_TOKEN=""
```

可选：

```
# 如需手动提供 tenant_access_token，可填（否则脚本会用 app_id/app_secret 获取）
# FEISHU_TENANT_ACCESS_TOKEN=""

# 若已知文档根块 ID
FEISHU_PARENT_BLOCK_ID=""

# 多维表格（可选）
FEISHU_BITABLE_NODE_TOKEN=""
FEISHU_BITABLE_TABLE_ID=""
```

2. 选择一个包含 Markdown 文件的目录作为“当前目录”。
   - 脚本会读取**当前目录**下找到的第一个 `*.md` 文件。

## 运行方式

从项目根目录执行（推荐）：

```bash
python tools/feishu/create_doc_from_md.py
```

或者在包含目标 Markdown 的目录执行：

```bash
cd /path/to/your/markdown
python /Users/andy/code/invest/invest_community/tools/feishu/create_doc_from_md.py
```

## 结果与输出

- 终端会打印新建文档的 `document_id` 与链接。
- 若多维表格缺少定位信息，会提示并跳过插入。

## 常见问题

- **提示缺少 FEISHU_SPACE_ID / FEISHU_PARENT_NODE_TOKEN**：这是新建节点必填项，请在 `.env` 填写。
- **创建嵌套块失败**：可能需要设置 `FEISHU_PARENT_BLOCK_ID` 为该文档根块 ID。
- **多维表格插入失败**：需要提供 `FEISHU_BITABLE_NODE_TOKEN` 与 `FEISHU_BITABLE_TABLE_ID`，并确保字段名匹配。
