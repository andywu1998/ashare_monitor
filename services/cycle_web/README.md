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
./start_service.sh start
```

然后打开：

- `http://127.0.0.1:8888/ui/`

说明：

- 本服务实际监听端口固定使用 `8888`。
- 如果通过内网穿透以 `14082` 等端口访问，那是外部映射端口，不是服务进程监听端口。
- `start_service.sh` 使用 systemd user service 托管，服务名为 `ashare-cycle-web.service`。

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
