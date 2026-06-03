#!/usr/bin/env python3
"""Debug Feishu Bitable: fetch schema and insert a record.

Usage examples:
  python tools/feishu/bitable_debug.py schema
  python tools/feishu/bitable_debug.py insert --fields '{"document_url": "https://example.com"}'
"""

import argparse
import csv
import json
import os
import ssl
import urllib.request
from pathlib import Path

BASE_URL = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn/open-apis")
REQUEST_TIMEOUT = int(os.getenv("FEISHU_REQUEST_TIMEOUT", "30"))


def _load_dotenv() -> None:
    project_root = Path(__file__).resolve().parents[2]
    env_path = project_root / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)

    if os.getenv("FEISHU_DISABLE_PROXY", "0") == "1":
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        urllib.request.install_opener(opener)


def _request(method: str, path: str, token: str | None, payload: dict | None = None) -> dict:
    if path.startswith("http://") or path.startswith("https://"):
        url = path
    else:
        url = f"{BASE_URL.rstrip('/')}{path}"

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = None
    if os.getenv("FEISHU_INSECURE_SSL", "0") == "1":
        context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=context) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_content = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_content)
            raise RuntimeError(f"飞书API请求失败 ({e.code}): {error_json}")
        except json.JSONDecodeError:
            raise RuntimeError(f"飞书API请求失败 ({e.code}): {error_content}")


def _get_access_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("缺少 FEISHU_APP_ID/FEISHU_APP_SECRET")

    payload = {"app_id": app_id, "app_secret": app_secret}
    res = _request("POST", "/auth/v3/tenant_access_token/internal", None, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {res}")
    return res["tenant_access_token"]


def _get_wiki_node(token: str, node_token: str, obj_type: str | None = None) -> dict:
    path = f"/wiki/v2/spaces/get_node?token={node_token}"
    if obj_type:
        path = f"{path}&obj_type={obj_type}"
    res = _request("GET", path, token, None)
    if res.get("code") != 0:
        raise RuntimeError(f"获取知识空间节点失败: {res}")
    return res.get("data", {}).get("node", {})


def _resolve_app_token(token: str) -> str:
    node_token = os.getenv("FEISHU_BITABLE_NODE_TOKEN")
    if not node_token:
        raise RuntimeError("缺少 FEISHU_BITABLE_NODE_TOKEN")
    # Try resolve wiki node -> obj_token (bitable app token)
    try:
        node = _get_wiki_node(token, node_token, "wiki")
        obj_token = node.get("obj_token")
        if obj_token:
            return obj_token
    except Exception:
        raise
    # Fallback: assume node_token is already app_token
    return node_token


def _get_table_id(override: str | None) -> str:
    if override:
        return override
    table_id = os.getenv("FEISHU_BITABLE_TABLE_ID")
    if not table_id:
        raise RuntimeError("缺少 FEISHU_BITABLE_TABLE_ID")
    return table_id


def _list_records(token: str, app_token: str, table_id: str) -> list[dict]:
    records = []
    page_token = ""
    while True:
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100"
        if page_token:
            path += f"&page_token={page_token}"
        res = _request("GET", path, token, None)
        if res.get("code") != 0:
            raise RuntimeError(f"获取记录失败: {res}")
        data = res.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
        if not page_token:
            break
    return records


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "是", "已上传", "已完成"}


def cmd_schema(token: str, table_id_override: str | None) -> int:
    app_token = _resolve_app_token(token)
    table_id = _get_table_id(table_id_override)
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    res = _request("GET", path, token, None)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_insert(token: str, fields_json: str | None) -> int:
    app_token = _resolve_app_token(token)
    table_id = _get_table_id(None)

    if fields_json:
        fields = json.loads(fields_json)
    else:
        # Fallback: use document_url from env if provided
        document_url = os.getenv("FEISHU_DOCUMENT_URL", "")
        if not document_url:
            raise RuntimeError("未提供 fields JSON，且 FEISHU_DOCUMENT_URL 为空")
        fields = {"document_url": document_url}

    payload = {"fields": fields}
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    res = _request("POST", path, token, payload)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_sync_csv(token: str, table_id_override: str | None, csv_path: str) -> int:
    app_token = _resolve_app_token(token)
    table_id = _get_table_id(table_id_override)
    csv_file = Path(csv_path).expanduser().resolve()
    if not csv_file.exists():
        raise RuntimeError(f"CSV 不存在: {csv_file}")

    records = _list_records(token, app_token, table_id)
    missing_entries = []
    for item in records:
        fields = item.get("fields", {}) or {}
        company = str(fields.get("company", "")).strip()
        complete = str(fields.get("complete", "")).strip()
        message_id = str(fields.get("message_id", "")).strip()
        if company and complete in {"0", "0.0", "False", "false", "否", "未完成", ""}:
            missing_entries.append((company, message_id))

    if not missing_entries:
        print("未找到 complete=0 的记录")
        return 0

    with csv_file.open("r", encoding="utf-8-sig", newline="") as f:
        reader = list(csv.reader(f))

    if not reader:
        raise RuntimeError("CSV 为空")

    header = reader[0]
    rows = reader[1:]
    message_idx = header.index("message_id") if "message_id" in header else None
    upload_idx = header.index("是否上传飞书") if "是否上传飞书" in header else None
    generate_idx = header.index("是否生成研报") if "是否生成研报" in header else None

    existing_message_ids = set()
    successful_message_ids = set()
    for row in rows:
        if not row:
            continue
        if message_idx is None or message_idx >= len(row):
            continue
        message_id = row[message_idx].strip()
        if not message_id:
            continue
        existing_message_ids.add(message_id)

        uploaded = upload_idx is not None and upload_idx < len(row) and _truthy(row[upload_idx])
        generated = generate_idx is not None and generate_idx < len(row) and _truthy(row[generate_idx])
        if uploaded or generated:
            successful_message_ids.add(message_id)

    new_rows = []
    for company, message_id in missing_entries:
        if not message_id:
            print(f"跳过缺少 message_id 的记录: {company}")
            continue
        # Never rewrite or requeue a task that already exists locally.
        if message_id in existing_message_ids or message_id in successful_message_ids:
            continue
        row = [company] + [""] * (len(header) - 1)
        if "message_id" in header:
            row[header.index("message_id")] = message_id
        new_rows.append(row)
        existing_message_ids.add(message_id)

    if not new_rows:
        print("CSV 已包含所有 message_id")
        return 0

    with csv_file.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in new_rows:
            writer.writerow(row)

    print(f"已追加 {len(new_rows)} 条到 CSV: {csv_file}")
    return 0


def main() -> int:
    _load_dotenv()
    token = _get_access_token()

    parser = argparse.ArgumentParser(description="Feishu Bitable debug tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    schema_parser = sub.add_parser("schema", help="print table schema")
    schema_parser.add_argument("--table-id", help="override table id")
    insert_parser = sub.add_parser("insert", help="insert a record")
    insert_parser.add_argument("--fields", help="JSON string for fields")
    sync_parser = sub.add_parser("sync-csv", help="sync incomplete records to CSV")
    sync_parser.add_argument("--table-id", help="override table id")
    sync_parser.add_argument(
        "--csv-path",
        default=str(Path(__file__).resolve().parents[1] / "company_report" / "tasks_template.csv"),
        help="CSV path to update",
    )

    args = parser.parse_args()

    if args.cmd == "schema":
        return cmd_schema(token, args.table_id)
    if args.cmd == "insert":
        return cmd_insert(token, args.fields)
    if args.cmd == "sync-csv":
        return cmd_sync_csv(token, args.table_id, args.csv_path)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
