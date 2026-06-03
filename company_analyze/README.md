# invest

以 `sync-run.sh` 为入口的研报生成与飞书同步脚本集。

## 入口

执行：

```bash
sh /vol1/1000/andyroot/code/invest/sync-run.sh
```

脚本会依次完成以下步骤：

1. 设置 `ARK_API_KEY`
2. 激活本地虚拟环境 `myenv`
3. 调用飞书表格同步脚本，拉取/更新待处理任务
4. 读取 CSV 任务列表，逐行生成研报并上传飞书

## 执行链路

`sync-run.sh`

- 导出火山 Ark API Key
- `source /vol1/1000/andyroot/code/invest/myenv/bin/activate`
- 执行 `invest_community/tools/feishu/bitable_debug.py sync-csv --table-id ...`
- 执行 `invest_community/tools/company_report/run_tasks_from_csv.py ... --record-dir /vol1/1000/andyroot/code/invest/record`

`run_tasks_from_csv.py`

- 读取 `tasks_template.csv`
- 自动识别“公司名称 / 研报文件路径 / 是否上传飞书 / 是否生成研报 / message_id”等列
- 如果某公司未生成研报，则调用 `generate_company_report.py`
- 如果研报已生成但未上传飞书，则调用 `create_doc_from_md.py`
- 使用 `record/` 目录下的标记文件避免同一 `message_id` 被重复处理

`generate_company_report.py`

- 调用 `llm_lib.huoshan.chat_stream`
- 当前使用火山 Ark `bots` 接口
- 当前模型参数是 `bot-20260130204017-f2rvb`
- 输出 Markdown 研报到 `invest_community/tools/company_report/reports/`

## Prompt 测试

如果只是想在修改 prompt 后快速验证生成效果，不建议重新走 `sync-run.sh` 的整条链路，因为前置的 `sync-csv` 可能覆盖本地测试任务。

推荐使用一键测试脚本：

```bash
sh /vol1/1000/andyroot/code/invest/run_report_test.sh 地平线机器人
```

这个脚本会：

1. 往 `tasks_template.csv` 追加一条未执行任务
2. 自动生成随机 `message_id`
3. 自动带上 `ARK_API_KEY`
4. 直接复用“同步 CSV 之后”的现有处理链路
5. 用 `myenv/bin/python3` 调用 `run_tasks_from_csv.py`

相关文件：

- `run_report_test.sh`: 一键测试入口
- `invest_community/tools/company_report/enqueue_csv_task.py`: 追加测试任务并接入后半段链路
- `invest_community/tools/company_report/run_tasks_from_csv.py`: 生成研报 + 上传飞书

## 主要目录

- `sync-run.sh`: 一键运行入口
- `run_report_test.sh`: prompt 调整后的一键测试入口
- `llm_lib/huoshan.py`: 火山 Ark 客户端封装
- `invest_community/tools/company_report/`: 研报生成与批处理逻辑
- `invest_community/tools/feishu/`: 飞书文档/多维表格同步逻辑
- `record/`: 并发处理标记目录
- `myenv/`: 当前脚本依赖的本地 Python 虚拟环境

## 研报 Prompt

当前研报生成要求包括：

- 最近一年公告、财报、新闻检索
- 最近 90 天内优先使用时效性更强的股价、机构观点和持仓信息
- Markdown 结构化输出
- 公司概览、商业模式、行业景气度、竞争格局
- 机构持仓与资金关注度
- 机构分析观点
- 财务指标、估值、催化剂、风险、结论
- 最近 30 天股价、成交量与事件催化分析
- 明确区分事实、市场观点、分析推断
- 信息不足时必须输出“未知/需验证”

Prompt 位置：

- `invest_community/tools/company_report/generate_company_report.py`

## 运行前提

- Python 虚拟环境存在：`/vol1/1000/andyroot/code/invest/myenv`
- 已安装脚本依赖
- 可访问火山 Ark 与飞书开放平台
- `tasks_template.csv` 中存在待处理任务

## 注意事项

- `sync-run.sh` 当前直接在脚本内导出 `ARK_API_KEY`，不适合提交真实生产密钥。
- 当前仓库里存在较多未清理的运行产物和环境目录，提交代码时应选择性暂存。
- `record/` 中的标记文件会影响重复执行结果；需要重跑某条任务时，应先清理对应 `message_id` 标记。
