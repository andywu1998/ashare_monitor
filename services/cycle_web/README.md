# Cycle Web Service (MVP)

把现有“生产数据 + 渲染 HTML 报告”的流程做成前后端服务。

## 功能

- 前端页面输入参数（公司名、threshold、min-gap、lookback）
- 后端实时生成周期分析 HTML
- 页面内预览报告 + 新窗口打开报告

## 目录

- `services/cycle_web/app.py`：FastAPI 后端
- `services/cycle_web/frontend/index.html`：前端页面
- `reports/service/`：后端生成的 HTML 与 metadata

## 启动

在 `ashare_monitor` 目录执行：

```bash
python -m pip install -r requirements.txt
MYSQL_PASSWORD='YOUR_MYSQL_PASSWORD' uvicorn services.cycle_web.app:app --host 0.0.0.0 --port 8090
```

然后打开：

- `http://127.0.0.1:8090/ui/`

## API

- `GET /api/health`
- `POST /api/reports`
- `GET /api/reports/{report_id}`
- `GET /api/reports/{report_id}/html`

`POST /api/reports` 示例 body：

```json
{
  "name": "中际旭创",
  "threshold": 0.08,
  "min_gap": 5,
  "lookback_days": 365,
  "mysql_host": "192.168.1.15",
  "mysql_user": "myuser",
  "mysql_database": "mydb",
  "mysql_password": "YOUR_MYSQL_PASSWORD"
}
```
