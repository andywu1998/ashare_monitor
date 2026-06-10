# company_analyze

以 `sync-run.sh` 为入口的研报生成与飞书同步脚本集。当前目录已经作为 `ashare_monitor/company_analyze` 的普通子目录纳入外层 Git 仓库，不再是独立 Git 仓库。

## 入口

在当前目录执行：

```bash
sh sync-run.sh
```

也可以绕过 CSV 队列，直接生成某一家公司的研报并上传飞书：

```bash
sh sync-run.sh --company 天通股份
```

也可以直接生成 A 股今日走势分析，并走同一套飞书 docx 上传和多维表格写入路径：

```bash
sh sync-run.sh --ashare-market-brief
```

如果要把这次上传关联到飞书多维表格中的某条任务，可以额外传入 `message_id`：

```bash
sh sync-run.sh --company 天通股份 --message-id om_xxx
```

线上定时任务按 10 秒间隔触发一次该脚本，日志写入：

```bash
logs/company_analyze_sync_cron.log
```

标准 crontab 不支持秒级调度，所以当前通过 6 条 cron 记录实现：每分钟分别在 `0/10/20/30/40/50` 秒执行一次。`sync-run.sh` 不再使用全局执行锁；同步 CSV 只使用短暂的 `sync-csv.lock`，具体研报任务通过 `record/<message_id>` 做任务级锁。

## 执行流程

`sync-run.sh` 的主流程分为两段：先从飞书多维表格同步待处理任务到本地 CSV，再逐条生成研报并上传飞书。

如果传入 `--company`，脚本会进入“直接生成模式”，跳过飞书表格同步和 CSV 队列，只执行单家公司研报生成与上传。

如果传入 `--ashare-market-brief`，脚本会进入“A 股市场复盘模式”，跳过飞书表格同步和 CSV 队列，先用 Tushare SDK 获取真实行情、成交额 Top、行业强弱、30 日走势和机构上下文，再生成 Markdown，并复用 `create_doc_from_md.py` 上传飞书和写入同一个多维表格。

### 1. 初始化运行环境

`sync-run.sh` 会先根据脚本所在目录计算路径：

- `ROOT_DIR`: 默认是当前 `company_analyze` 目录
- `VENV_DIR`: 默认是 `$ROOT_DIR/.venv`
- `VENV_PY`: `$VENV_DIR/bin/python3`
- `RECORD_DIR`: `$ROOT_DIR/record`
- `SYNC_LOCK_DIR`: `$ROOT_DIR/record/sync-csv.lock`

然后做这些检查和设置：

- 检查 `.venv/bin/python3` 是否存在且可执行
- 检查 `lark-cli` 是否存在，或使用 `LARK_CLI_BIN` 指定的路径
- 创建 `record/` 目录
- 不创建全局运行锁；多个进程可以同时进入任务执行阶段
- 用 `record/sync-csv.lock` 保护飞书表格同步到 CSV 的短临界区；如果该锁已存在，会跳过本次 CSV 同步，但仍继续执行本地 CSV 中的任务
- 读取 `invest_community/.env`，把里面的变量导出到当前进程
- 检查 `ARK_API_KEY`，没有则退出
- 设置 `HOME`、`USER`、`LOGNAME`、`XDG_DATA_HOME`、`XDG_CONFIG_HOME`
- 把 `lark-cli` 所在目录和 `.venv/bin` 放进 `PATH`
- 设置默认 `FEISHU_LARK_CLI_PROFILE=company_analyze`

### 直接生成模式

入口命令：

```bash
sh sync-run.sh --company <公司名> [--message-id <message_id>]
```

执行逻辑：

- 复用 `sync-run.sh` 的环境初始化
- 如果未传 `--message-id`，脚本会自动生成 `om_` 开头的随机 `message_id`
- 使用 `record/<message_id>` 做任务级锁，避免同一个 message_id 被重复生成
- 不读取飞书多维表格
- 不读取或更新 `tasks_template.csv`
- 直接调用 `generate_company_report.py <公司名>`
- 从生成脚本输出中解析 `已生成报告：<path>`
- 直接调用 `create_doc_from_md.py <path>` 上传飞书
- 上传时一定会设置 `FEISHU_MESSAGE_ID`，飞书多维表格写回记录会携带该 `message_id`

适用场景：

- 临时生成某家公司研报
- 不希望污染或依赖 CSV 队列
- 想快速验证 prompt、行情注入、火山生成和飞书上传整条链路

示例：

```bash
sh sync-run.sh --company 黑芝麻智能
```

带 message_id：

```bash
sh sync-run.sh --company 黑芝麻智能 --message-id om_x100xxxx
```

### A 股市场复盘模式

入口命令：

```bash
sh sync-run.sh --ashare-market-brief [--date latest|YYYYMMDD] [--message-id om_xxx]
```

可选参数：

```bash
--top-n 20
--lookback 30
--institution-top-n 5
--institution-lookback-days 365
```

执行逻辑：

- 自动生成 `om_` 开头的随机 `message_id`，除非显式传入 `--message-id`
- 使用 `record/<message_id>` 做任务级锁
- 调用 `invest_community/tools/company_report/generate_ashare_market_brief.py`
- 生成 Markdown 到 `invest_community/tools/company_report/reports/`
- 调用 `invest_community/tools/feishu/create_doc_from_md.py <path>`
- 上传和写入多维表格路径与公司研报完全一致
- 上传时设置 `FEISHU_MESSAGE_ID`，多维表格记录会携带该 `message_id`

数据来源：

- Tushare SDK
- 指数日线、全市场日线、daily_basic、moneyflow（如可用）
- 成交额 Top 个股最近 30 个交易日走势
- 前十大流通股东、研报评级、机构调研等机构上下文（受 Tushare 权限和频率限制，失败会写入取数备注）

示例：

```bash
sh sync-run.sh --ashare-market-brief --top-n 20 --lookback 30 --institution-top-n 5
```

### 2. 同步飞书多维表格到 CSV

入口命令：

```bash
.venv/bin/python3 invest_community/tools/feishu/bitable_debug.py sync-csv --table-id tblnCWQYqUXp3tmU
```

对应文件：

```text
invest_community/tools/feishu/bitable_debug.py
```

执行逻辑：

- 从 `invest_community/.env` 读取飞书配置
- 用 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 获取 `tenant_access_token`
- 用 `FEISHU_BITABLE_NODE_TOKEN` 定位多维表格 app token
- 读取指定 table 的 records
- 筛选 `complete` 为未完成的记录
- 提取 `company` 和 `message_id`
- 写入本地 CSV：

```text
invest_community/tools/company_report/tasks_template.csv
```

去重规则：

- 如果本地 CSV 已存在相同 `message_id`，不会重复追加
- 如果该 `message_id` 对应任务已经生成或上传，也不会重新排队
- 如果飞书没有未完成记录，会输出 `未找到 complete=0 的记录`
- 如果本地已经包含所有待处理 `message_id`，会输出 `CSV 已包含所有 message_id`

### 3. 执行 CSV 中的研报任务

入口命令：

```bash
.venv/bin/python3 invest_community/tools/company_report/run_tasks_from_csv.py \
  invest_community/tools/company_report/tasks_template.csv \
  --record-dir record
```

对应文件：

```text
invest_community/tools/company_report/run_tasks_from_csv.py
```

执行逻辑：

- 读取 `tasks_template.csv`
- 自动识别这些列：
  - 公司名
  - 研报文件路径
  - 是否上传飞书
  - 是否生成研报
  - `message_id`
- 对每一行判断是否需要处理：
  - 如果报告路径为空或文件不存在，认为需要生成研报
  - 如果报告已存在但未上传，认为需要上传飞书
  - 如果报告存在且已上传，跳过
- 对每个 `message_id` 创建 `record/<message_id>` 标记文件，避免重复处理；不同 `message_id` 可以被不同进程并发处理
- 标记文件默认超过 `REPORT_TASK_STALE_MARKER_SECONDS=900` 秒会被视为陈旧并回收
- 生成或上传成功后，只把当前 `message_id` 对应行合并写回最新 CSV，避免并发进程互相覆盖状态
- 最多跑 `--max-passes` 轮，默认 5 轮

### 4. 生成研报

`run_tasks_from_csv.py` 会调用：

```bash
.venv/bin/python3 invest_community/tools/company_report/generate_company_report.py <公司名> --message-id <message_id>
```

对应文件：

```text
invest_community/tools/company_report/generate_company_report.py
```

生成逻辑：

- 读取公司名
- 检查 `ARK_API_KEY`
- 构造研报 prompt
- 调用 `llm_lib.huoshan.chat_stream`
- 使用火山 Ark OpenAI 兼容接口
- 当前默认 bot/model 在 `llm_lib/huoshan.py` 中配置
- 输出 Markdown 研报到：

```text
invest_community/tools/company_report/reports/
```

文件名格式类似：

```text
公司名_YYYYMMDD_HHMMSS_report.md
```

如果设置：

```bash
export HUOSHAN_MOCK=1
```

则不会调用真实火山接口，而是生成一份本地模拟研报，用于调试链路。

### 5. 注入最近 30 个交易日行情数据

研报 prompt 会先调用：

```text
invest_community/tools/company_report/market_data.py
```

该模块负责获取最近 30 个交易日股价，并把行情摘要和表格注入 prompt。

处理逻辑：

- 读取 `TUSHARE_TOKEN`
- 如果当前环境没有 `TUSHARE_TOKEN`，会尝试解析 `~/.zshrc` 中的简单 `export TUSHARE_TOKEN=...` 配置
- 先用 `stock_basic` 查询 A 股基础信息
- 再用 AkShare/Sina 的 `stock_hk_spot()` 查询港股基础信息
- 港股基础信息会缓存到 `record/market_data_cache/hk_spot_akshare_sina.json`，默认 24 小时有效，避免每次生成研报都拉取全量港股列表
- 匹配策略是先在 A/H 两个市场做精确匹配，再退回模糊匹配
- A 股行情使用 `pro.daily`
- 港股行情使用 AkShare/Sina 的 `stock_hk_daily(symbol=<港股代码>, adjust="")`
- 港股数据源与外层 `ashare_monitor` 的港股定时同步任务保持一致；当前 crontab 使用 `scripts/run_hk_sync_all_concurrent.py --provider akshare_sina`
- 拉取最近约 90 个自然日数据，再截取最近 30 个交易日
- 输出内容包括：
  - 匹配到的公司名、代码、市场类型
  - 行情数据源
  - 数据区间
  - 区间收盘涨跌幅
  - 区间最高价、最低价
  - 平均成交额/成交金额字段
  - 每日开高低收、涨跌幅、成交量、成交额/金额

如果行情获取失败，生成流程不会中断；prompt 中会写明失败原因，并要求模型在近 30 天股价部分标注“未知/需验证”。

### 6. 上传飞书文档

如果研报文件已生成但还没上传，`run_tasks_from_csv.py` 会调用：

```bash
.venv/bin/python3 invest_community/tools/feishu/create_doc_from_md.py <研报 Markdown 路径>
```

对应文件：

```text
invest_community/tools/feishu/create_doc_from_md.py
```

上传逻辑：

- 读取 `invest_community/.env`
- 获取飞书 `tenant_access_token`
- 默认使用 `lark-cli` 创建 wiki docx 节点
- 用 `lark-cli docs +update` 把 Markdown 内容覆盖写入文档
- 生成飞书 wiki 链接
- 如果 `FEISHU_SKIP_BITABLE` 不是 `1`，则把文档信息写回多维表格
- 如果存在 `FEISHU_MESSAGE_ID`，会把它写入多维表格字段，方便关联原始任务

默认文档创建方式是 `lark-cli`。如果设置：

```bash
export FEISHU_USE_OPENAPI=1
```

则走 OpenAPI 创建文档和写入 blocks 的备用路径。

## CSV 状态字段

`tasks_template.csv` 至少需要这些列：

```text
公司名称,研报文件路径,是否上传飞书,是否生成研报,message_id
```

状态含义：

- `研报文件路径` 为空：需要生成研报
- `研报文件路径` 有值但文件不存在：需要重新生成研报
- `是否上传飞书` 为空或否：需要上传飞书
- `是否生成研报` / `是否上传飞书` 为 `是`：表示对应步骤已完成
- `message_id`：用于与飞书多维表格任务关联，也用于本地并发标记

`tasks_template.csv` 是运行状态文件，已加入 `.gitignore`，不应该提交。

## 重跑某条任务

如果要重跑某个 `message_id`：

1. 在 `tasks_template.csv` 中找到对应行
2. 清空 `研报文件路径`
3. 清空 `是否上传飞书`
4. 清空 `是否生成研报`
5. 删除可能残留的本地标记文件：

```bash
rm -f record/<message_id>
```

下一次 `sync-run.sh` 执行时会重新生成研报并上传飞书。

## 运行前提

- 当前目录存在 `.venv/bin/python3`
- `.venv` 已安装：
  - `openai`
  - `tushare`
  - `pandas`
- 本机能访问火山 Ark、Tushare、飞书开放平台
- 本机能执行 `lark-cli`
- `invest_community/.env` 中配置飞书与火山相关变量
- `TUSHARE_TOKEN` 在环境变量、`invest_community/.env` 或 `~/.zshrc` 中可用

## 关键环境变量

必需：

- `ARK_API_KEY`: 火山 Ark API Key
- `FEISHU_APP_ID`: 飞书应用 ID
- `FEISHU_APP_SECRET`: 飞书应用 Secret
- `FEISHU_SPACE_ID`: 飞书知识库空间 ID
- `FEISHU_PARENT_NODE_TOKEN`: 新建文档的父节点 token
- `FEISHU_BITABLE_NODE_TOKEN`: 多维表格 wiki node token
- `FEISHU_BITABLE_TABLE_ID`: 多维表格 table id
- `TUSHARE_TOKEN`: Tushare token，用于最近 30 个交易日行情注入

常用可选：

- `LARK_CLI_BIN`: 指定 `lark-cli` 路径
- `FEISHU_LARK_CLI_PROFILE`: 指定 lark-cli profile，默认 `company_analyze`
- `FEISHU_USE_OPENAPI=1`: 不使用 lark-cli，改走 OpenAPI 文档写入路径
- `FEISHU_SKIP_BITABLE=1`: 上传文档后不写回多维表格
- `REPORT_TASK_STALE_MARKER_SECONDS`: record 标记过期秒数，默认 900
- `HUOSHAN_MOCK=1`: 本地模拟研报生成，不调用火山接口

## 主要目录

- `sync-run.sh`: 主入口
- `run_report_test.sh`: 手动追加测试任务入口
- `llm_lib/huoshan.py`: 火山 Ark 客户端封装
- `invest_community/tools/feishu/bitable_debug.py`: 飞书多维表格读取与 CSV 同步
- `invest_community/tools/company_report/run_tasks_from_csv.py`: CSV 任务执行器
- `invest_community/tools/company_report/generate_company_report.py`: 研报生成脚本
- `invest_community/tools/company_report/market_data.py`: Tushare 最近 30 个交易日行情上下文
- `invest_community/tools/feishu/create_doc_from_md.py`: Markdown 上传飞书文档并写回多维表格
- `invest_community/tools/company_report/reports/`: 生成的 Markdown 研报
- `record/`: 并发锁和 message_id 标记目录
- `logs/`: cron 运行日志

## 注意事项

- `sync-run.sh` 是幂等触发设计，可以高频执行；同一个任务的防重入依赖 `record/<message_id>`
- 研报生成耗时较长时，后续 10 秒 cron 仍可拉取新任务；如果新任务有不同 `message_id`，可以并发处理
- `run_tasks_from_csv.py` 会捕获生成和上传子进程输出，所以 cron 日志里不一定能看到模型流式内容
- 如果想观察模型实时输出，可以直接手动运行 `generate_company_report.py`
- `tasks_template.csv`、`reports/`、`record/`、`.env`、`.venv` 都属于本地运行状态或敏感文件，不应提交
