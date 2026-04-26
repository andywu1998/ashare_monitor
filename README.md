# A-Share Monitor

A 股日常监控、周期分析与股票数据同步工具集。

当前工作区包含四类能力：

- 日报采集与文本总结（可选 Codex 点评）
- 全市场成交额/成交量 TopN 排行
- 基于 MySQL 日线数据的周期分析（脚本 + Web 服务）
- 基于 Tushare 的股票基础信息与全历史日线入库（MySQL）

## 目录结构

```text
ashare_monitor/
├── ashare_monitor/
│   ├── ...                        # 原有监控与报告模块
│   └── stock_sync/                # 新整合：Tushare -> MySQL 同步模块
│       ├── config.py
│       ├── db.py
│       ├── fetch_one.py
│       ├── fetch_all.py
│       ├── fetch_all_concurrent.py
│       └── schema.sql
├── scripts/                       # 统一入口脚本
│   ├── run_daily.py
│   ├── run_top10_volume.py
│   ├── run_cycle_report.py
│   ├── run_stock_sync_one.py
│   ├── run_stock_sync_all.py
│   └── run_stock_sync_all_concurrent.py
├── services/
│   └── cycle_web/
├── configs/
│   ├── config.example.toml
│   ├── config.toml
│   ├── config.legacy.yml
│   ├── stock_sync.env.example
│   └── stock_sync.env             # 本地私有，已加入 .gitignore
├── reports/
├── docs/
├── start_service.sh
├── requirements.txt
└── README.md
```

## 环境要求

- Python 3.11+
- `mysql` 命令行客户端（周期分析脚本依赖）
- MySQL / MariaDB（周期分析与数据同步依赖）
- 可联网（Sina / 东财 / Tushare）
- 可选：`codex` CLI（日报 AI 点评）

安装依赖：

```bash
cd /home/admin/code/cc-connect-work-space/ashare_monitor
python3 -m venv .venv_web
source .venv_web/bin/activate
pip install -r requirements.txt
```

## 配置

### 1) 日报/监控配置（TOML）

```bash
cp configs/config.example.toml configs/config.toml
```

默认读取：`configs/config.toml`。

### 2) 股票同步配置（ENV）

```bash
cp configs/stock_sync.env.example configs/stock_sync.env
```

`configs/stock_sync.env` 关键字段：

- `TUSHARE_TOKEN`
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `MYSQL_DATABASE`

## 使用方式

### 1) 生成 A 股日报

```bash
python3 scripts/run_daily.py --provider sina
```

### 2) 生成成交额/成交量 Top10

```bash
python3 scripts/run_top10_volume.py --sort-by amount --top-k 10
```

### 3) 生成周期分析 HTML（脚本）

```bash
python3 scripts/run_cycle_report.py \
  --name 中际旭创 \
  --host 192.168.1.15 \
  --user myuser \
  --password 'YOUR_PASSWORD' \
  --database mydb
```

### 4) 股票数据同步（Tushare -> MySQL）

同步模块会默认读取 `configs/stock_sync.env`。

单只股票（代码在 `ashare_monitor/stock_sync/fetch_one.py` 的 `TARGET_TS_CODE`）：

```bash
python3 scripts/run_stock_sync_one.py
```

全量串行同步：

```bash
python3 scripts/run_stock_sync_all.py
```

全量并发同步（推荐）：

```bash
python3 scripts/run_stock_sync_all_concurrent.py --concurrency 4 --on-alert continue
```

说明：

- 首次运行会按 `ashare_monitor/stock_sync/schema.sql` 自动建表（`stock_basic` / `stock_daily`）
- 表存在时会做 UPSERT，支持重复执行

## 启动周期分析 Web 服务

注意：

- 服务监听端口固定为本机 `8888`。
- 若使用内网穿透（例如外部访问 `14082`），那只是外部映射端口，服务本机仍是 `8888`。
- `start_service.sh` 现在基于 **systemd user service** 管理（服务名：`ashare-cycle-web.service`）。

```bash
./start_service.sh start
```

查看状态：

```bash
./start_service.sh status
```

查看日志：

```bash
./start_service.sh logs
```

若需要让用户服务在退出图形会话后仍可运行，可执行：

```bash
sudo loginctl enable-linger admin
```

访问：

- UI：`http://127.0.0.1:8888/ui/`
- 健康检查：`http://127.0.0.1:8888/api/health`
