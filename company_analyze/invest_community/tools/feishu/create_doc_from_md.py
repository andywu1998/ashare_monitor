#!/usr/bin/env python3
"""Create a Feishu docx from a local Markdown file and insert into a Bitable table.

Steps implemented per 新建步骤.md:
1) Create wiki node (docx)
2) Convert markdown to docx blocks
3) Create nested blocks under document
4) Insert doc link into Bitable (placeholder if unknown)
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import ssl
from pathlib import Path

BASE_URL = os.getenv("FEISHU_BASE_URL", "https://open.feishu.cn/open-apis")
def _get_timeout() -> int:
    return int(os.getenv("FEISHU_REQUEST_TIMEOUT", "30"))


def _get_retry() -> int:
    return int(os.getenv("FEISHU_REQUEST_RETRY", "2"))


def _get_backoff() -> float:
    return float(os.getenv("FEISHU_RETRY_BACKOFF", "1.0"))


def _skip_bitable() -> bool:
    return os.getenv("FEISHU_SKIP_BITABLE", "1") == "1"


def _load_dotenv() -> None:
    """Load key=value pairs from project .env into environment."""
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
    last_err: Exception | None = None
    retries = _get_retry()
    timeout = _get_timeout()
    backoff = _get_backoff()
    context = None
    if os.getenv("FEISHU_INSECURE_SSL", "0") == "1":
        context = ssl._create_unverified_context()
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="ignore")
            except Exception:
                body = ""
            raise RuntimeError(
                f"请求失败: {url} (HTTP {exc.code}) {body}"
            ) from exc
        except urllib.error.URLError as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RuntimeError(f"请求超时或网络错误: {url} ({exc})") from exc
        except Exception as exc:
            raise RuntimeError(f"请求失败: {url} ({exc})") from exc

    raise RuntimeError(f"请求失败: {url} ({last_err})")


def _get_access_token() -> str:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("缺少 FEISHU_APP_ID/FEISHU_APP_SECRET，无法获取 tenant_access_token")

    payload = {"app_id": app_id, "app_secret": app_secret}
    res = _request("POST", "/auth/v3/tenant_access_token/internal", None, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"获取 tenant_access_token 失败: {res}")
    return res["tenant_access_token"]


def _find_markdown_file(cli_path: str | None = None) -> Path:
    # Priority: CLI argument > environment variable > auto-detect
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    explicit_path = os.getenv("FEISHU_MARKDOWN_PATH")
    if explicit_path:
        return Path(explicit_path).expanduser().resolve()

    # Pick the first markdown file in current directory.
    candidates = sorted(Path.cwd().glob("*.md"))
    if not candidates:
        raise RuntimeError("当前目录没有找到 .md 文件")
    return candidates[0]


def _find_lark_cli() -> str:
    candidates = [
        os.getenv("LARK_CLI_BIN"),
        shutil.which("lark-cli"),
        str(Path.home() / ".nvm" / "versions" / "node" / "v24.14.0" / "bin" / "lark-cli"),
        str(Path.home() / ".nvm" / "versions" / "node" / "current" / "bin" / "lark-cli"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("未找到 larksuite/cli 可执行文件（lark-cli）")


def _parse_lark_cli_json(stdout: str, command_name: str) -> dict:
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    payload_start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("{")), None)
    if payload_start is None:
        raise RuntimeError(f"无法解析 {command_name} 输出: {stdout}")
    return json.loads("\n".join(lines[payload_start:]))


def _run_lark_cli(cmd: list[str], cwd: str | None = None) -> dict:
    profile = os.getenv("FEISHU_LARK_CLI_PROFILE", "").strip()
    if profile:
        cmd = [cmd[0], "--profile", profile, *cmd[1:]]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"lark-cli 执行失败: {exc.stderr.strip() or exc.stdout.strip()}"
        ) from exc
    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("lark-cli 未返回结果")
    return _parse_lark_cli_json(stdout, "lark-cli")


def _create_wiki_node_via_lark_cli(title: str) -> tuple[str, str]:
    space_id = os.getenv("FEISHU_SPACE_ID")
    parent_node_token = os.getenv("FEISHU_PARENT_NODE_TOKEN")
    node_type = os.getenv("FEISHU_NODE_TYPE", "origin")
    obj_type = os.getenv("FEISHU_OBJ_TYPE", "docx")
    if not space_id or not parent_node_token:
        raise RuntimeError("缺少 FEISHU_SPACE_ID 或 FEISHU_PARENT_NODE_TOKEN")

    cli_path = _find_lark_cli()
    cmd = [
        cli_path,
        "wiki",
        "+node-create",
        "--as",
        "bot",
        "--space-id",
        space_id,
        "--parent-node-token",
        parent_node_token,
        "--node-type",
        node_type,
        "--obj-type",
        obj_type,
        "--title",
        title,
        "--format",
        "json",
    ]
    payload = _run_lark_cli(cmd)
    data = payload.get("data", {})
    node = data.get("node", data)
    obj_token = node.get("obj_token")
    node_token = node.get("node_token")
    if not obj_token or not node_token:
        raise RuntimeError(f"lark-cli 创建 wiki 节点返回缺少 obj_token/node_token: {payload}")
    return obj_token, node_token


def _update_doc_via_lark_cli(document_id: str, title: str, md_path: Path) -> None:
    cli_path = _find_lark_cli()
    cmd = [
        cli_path,
        "docs",
        "+update",
        "--api-version",
        "v2",
        "--as",
        "bot",
        "--doc",
        document_id,
        "--command",
        "overwrite",
        "--content",
        f"@./{md_path.name}",
        "--new-title",
        title,
        "--format",
        "json",
    ]
    _run_lark_cli(cmd, cwd=str(md_path.parent))


def _create_doc_via_lark_cli(md_path: Path, title: str) -> tuple[str, str]:
    document_id, node_token = _create_wiki_node_via_lark_cli(title)
    _update_doc_via_lark_cli(document_id, title, md_path)
    base_web_url = os.getenv("FEISHU_WEB_BASE_URL", "https://my.feishu.cn")
    wiki_url = f"{base_web_url}/wiki/{node_token}"
    return document_id, wiki_url


def _create_doc_via_openapi(token: str, markdown_text: str, title: str) -> tuple[str, str]:
    document_id, node_token = _create_wiki_node(token, title)
    blocks = _simple_markdown_to_blocks(markdown_text)
    blocks = _convert_table_to_text(_flatten_blocks(_sanitize_blocks(blocks)))
    _create_nested_blocks(token, document_id, blocks)
    base_web_url = os.getenv("FEISHU_WEB_BASE_URL", "https://my.feishu.cn")
    wiki_url = f"{base_web_url}/wiki/{node_token}"
    return document_id, wiki_url


def _create_wiki_node(token: str, title: str) -> tuple[str, str]:
    space_id = os.getenv("FEISHU_SPACE_ID")
    parent_node_token = os.getenv("FEISHU_PARENT_NODE_TOKEN")
    node_type = os.getenv("FEISHU_NODE_TYPE", "origin")
    obj_type = os.getenv("FEISHU_OBJ_TYPE", "docx")

    if not space_id or not parent_node_token:
        raise RuntimeError("缺少 FEISHU_SPACE_ID 或 FEISHU_PARENT_NODE_TOKEN")

    payload = {
        "parent_node_token": parent_node_token,
        "obj_type": obj_type,
        "title": title,
    }
    if node_type:
        payload["node_type"] = node_type
    path = f"/wiki/v2/spaces/{space_id}/nodes"
    res = _request("POST", path, token, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"创建空间节点失败: {res}")
    node = res["data"]["node"]
    return node["obj_token"], node["node_token"]


def _convert_markdown_to_blocks(token: str, markdown_text: str) -> list:
    payload = {
        "content": markdown_text,
        "type": "markdown",
        "content_type": "markdown",
    }
    candidates = [
        "/docx/v1/documents/blocks/convert",
        "/docx/v1/document/convert",
        "/docx/v1/documents/convert",
    ]
    # Fallback to base URL without /open-apis (some deployments differ).
    base_no_open_apis = BASE_URL.replace("/open-apis", "")
    candidates.extend(
        [
            f"{base_no_open_apis.rstrip('/')}/docx/v1/document/convert",
            f"{base_no_open_apis.rstrip('/')}/docx/v1/documents/convert",
        ]
    )

    last_exc: RuntimeError | None = None
    for path in candidates:
        try:
            res = _request("POST", path, token, payload)
            break
        except RuntimeError as exc:
            last_exc = exc
            if "HTTP 404" in str(exc):
                continue
            raise
    else:
        raise last_exc if last_exc else RuntimeError("Markdown 转换失败：未知错误")
    if res.get("code") != 0:
        raise RuntimeError(f"Markdown 转换失败: {res}")
    return res["data"].get("blocks", [])


def _parse_inline_styles(text: str) -> list:
    """解析行内样式（粗体、斜体）并返回 elements 数组"""
    import re

    elements = []
    current_pos = 0

    # 匹配粗体 **text** 和斜体 *text*
    # 注意：需要先匹配粗体（**），再匹配斜体（*）
    pattern = r'(\*\*\*(.+?)\*\*\*|\*\*(.+?)\*\*|\*(.+?)\*)'

    for match in re.finditer(pattern, text):
        # 添加匹配前的普通文本
        if match.start() > current_pos:
            plain_text = text[current_pos:match.start()]
            if plain_text:
                elements.append({
                    "text_run": {
                        "content": plain_text,
                        "text_element_style": {
                            "bold": False,
                            "inline_code": False,
                            "italic": False,
                            "strikethrough": False,
                            "underline": False
                        }
                    }
                })

        # 添加样式化的文本
        if match.group(2):  # ***text*** (粗体+斜体)
            content = match.group(2)
            bold = True
            italic = True
        elif match.group(3):  # **text** (粗体)
            content = match.group(3)
            bold = True
            italic = False
        else:  # *text* (斜体)
            content = match.group(4)
            bold = False
            italic = True

        elements.append({
            "text_run": {
                "content": content,
                "text_element_style": {
                    "bold": bold,
                    "inline_code": False,
                    "italic": italic,
                    "strikethrough": False,
                    "underline": False
                }
            }
        })

        current_pos = match.end()

    # 添加剩余的普通文本
    if current_pos < len(text):
        plain_text = text[current_pos:]
        if plain_text:
            elements.append({
                "text_run": {
                    "content": plain_text,
                    "text_element_style": {
                        "bold": False,
                        "inline_code": False,
                        "italic": False,
                        "strikethrough": False,
                        "underline": False
                    }
                }
            })

    # 如果没有任何元素，返回一个空文本元素
    if not elements:
        elements.append({
            "text_run": {
                "content": "",
                "text_element_style": {
                    "bold": False,
                    "inline_code": False,
                    "italic": False,
                    "strikethrough": False,
                    "underline": False
                }
            }
        })

    return elements


def _simple_markdown_to_blocks(markdown_text: str) -> list:
    blocks = []
    lines = markdown_text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # 跳过空行
        if not line:
            i += 1
            continue

        # 跳过分隔线（不创建块，只是跳过）
        if line.strip() in ['---', '***', '___']:
            i += 1
            continue

        # 标题
        if line.startswith('# '):
            content = line[2:].strip()
            block = {
                "block_type": 3,
                "heading1": {
                    "elements": _parse_inline_styles(content),
                    "style": {"align": 1, "folded": False}
                }
            }
            blocks.append(block)
            i += 1
            continue

        if line.startswith('## '):
            content = line[3:].strip()
            block = {
                "block_type": 4,
                "heading2": {
                    "elements": _parse_inline_styles(content),
                    "style": {"align": 1, "folded": False}
                }
            }
            blocks.append(block)
            i += 1
            continue

        if line.startswith('### '):
            content = line[4:].strip()
            block = {
                "block_type": 5,
                "heading3": {
                    "elements": _parse_inline_styles(content),
                    "style": {"align": 1, "folded": False}
                }
            }
            blocks.append(block)
            i += 1
            continue

        # 列表项 - 转换为普通文本，保留列表标记
        if line.lstrip().startswith('* ') or line.lstrip().startswith('- '):
            # 保留缩进和列表标记
            content = line
            block = {
                "block_type": 2,
                "text": {
                    "elements": _parse_inline_styles(content),
                    "style": {"align": 1, "folded": False}
                }
            }
            blocks.append(block)
            i += 1
            continue

        # 有序列表 - 转换为普通文本，保留数字标记
        import re
        if re.match(r'^(\s*)(\d+)\.\s+', line):
            content = line
            block = {
                "block_type": 2,
                "text": {
                    "elements": _parse_inline_styles(content),
                    "style": {"align": 1, "folded": False}
                }
            }
            blocks.append(block)
            i += 1
            continue

        # 普通文本
        content = line
        block = {
            "block_type": 2,
            "text": {
                "elements": _parse_inline_styles(content),
                "style": {"align": 1, "folded": False}
            }
        }
        blocks.append(block)
        i += 1

    return blocks


def _convert_table_to_text(blocks: list) -> list:
    """将表格块转换为文本块"""
    converted = []
    i = 0

    while i < len(blocks):
        block = blocks[i]

        # 如果是表格块（block_type 31），尝试转换
        if isinstance(block, dict) and block.get('block_type') == 31:
            # 收集表格的所有单元格
            table_cells = []
            i += 1

            # 收集后续的 table_cell 块
            while i < len(blocks) and blocks[i].get('block_type') == 32:
                table_cells.append(blocks[i])
                i += 1

            # 将表格转换为文本
            if table_cells:
                # 提取单元格内容
                cell_contents = []
                for cell in table_cells:
                    if 'table_cell' in cell:
                        elements = cell['table_cell'].get('elements', [])
                        content = ''.join(
                            elem.get('text_run', {}).get('content', '')
                            for elem in elements
                        )
                        cell_contents.append(content.strip())

                # 创建文本块显示表格内容
                if cell_contents:
                    # 简单格式：每个单元格一行，使用 | 分隔
                    table_text = '\n'.join(f"| {content}" for content in cell_contents if content)

                    converted.append({
                        "block_type": 2,
                        "text": {
                            "elements": [{
                                "text_run": {
                                    "content": "[表格内容]\n" + table_text,
                                    "text_element_style": {
                                        "bold": False,
                                        "inline_code": False,
                                        "italic": False,
                                        "strikethrough": False,
                                        "underline": False
                                    }
                                }
                            }],
                            "style": {"align": 1, "folded": False}
                        }
                    })
            continue

        # 如果是独立的 table_cell（不应该发生，但以防万一）
        if isinstance(block, dict) and block.get('block_type') == 32:
            # 转换为文本块
            if 'table_cell' in block:
                elements = block['table_cell'].get('elements', [])
                content = ''.join(
                    elem.get('text_run', {}).get('content', '')
                    for elem in elements
                )
                if content.strip():
                    converted.append({
                        "block_type": 2,
                        "text": {
                            "elements": [{
                                "text_run": {
                                    "content": content,
                                    "text_element_style": {
                                        "bold": False,
                                        "inline_code": False,
                                        "italic": False,
                                        "strikethrough": False,
                                        "underline": False
                                    }
                                }
                            }],
                            "style": {"align": 1, "folded": False}
                        }
                    })
            i += 1
            continue

        # 其他块直接保留
        converted.append(block)
        i += 1

    return converted


def _flatten_blocks(blocks: list) -> list:
    """展平嵌套的块结构，将所有 children 提取到顶层"""
    flattened = []

    def _flatten_block(block: dict):
        # 提取 children
        children = block.pop("children", [])

        # 添加当前块（不包含 children）
        flattened.append(block)

        # 递归处理 children
        for child in children:
            if isinstance(child, dict):
                _flatten_block(child)

    for block in blocks:
        if isinstance(block, dict):
            _flatten_block(block.copy())

    return flattened


def _sanitize_blocks(blocks: list) -> list:
    def _sanitize_block(block: dict) -> dict:
        cleaned = {}
        for key, value in block.items():
            if key in {"block_id", "parent_id", "document_id", "revision_id", "create_time", "update_time"}:
                continue
            if key.endswith("_id") and key != "block_type":
                continue
            if key in {"children"} and isinstance(value, list):
                cleaned[key] = [
                    _sanitize_block(child) for child in value if isinstance(child, dict)
                ]
                continue
            cleaned[key] = value
        return cleaned

    return [_sanitize_block(block) for block in blocks if isinstance(block, dict)]

def _get_document_blocks(token: str, document_id: str) -> list:
    path = f"/docx/v1/documents/{document_id}/blocks?document_revision_id=-1&page_size=500"
    res = _request("GET", path, token, None)
    if res.get("code") != 0:
        raise RuntimeError(f"获取文档块失败: {res}")
    return res.get("data", {}).get("items", [])


def _get_wiki_node(token: str, node_token: str, obj_type: str | None = None) -> dict:
    path = f"/wiki/v2/spaces/get_node?token={node_token}"
    if obj_type:
        path = f"{path}&obj_type={obj_type}"
    res = _request("GET", path, token, None)
    if res.get("code") != 0:
        raise RuntimeError(f"获取知识空间节点失败: {res}")
    return res.get("data", {}).get("node", {})


def _get_document_root_block_id(token: str, document_id: str) -> str | None:
    path = f"/docx/v1/documents/{document_id}"
    res = _request("GET", path, token, None)
    if res.get("code") != 0:
        return None
    data = res.get("data", {})
    document = data.get("document", {}) if isinstance(data, dict) else {}
    if os.getenv("FEISHU_DEBUG", "0") == "1":
        print(f"document info: {document}")
    # Try common root fields.
    for key in ("root_id", "root_block_id", "block_id"):
        value = document.get(key)
        if value:
            return value
    return None


def _get_document_revision_id(token: str, document_id: str) -> int | None:
    path = f"/docx/v1/documents/{document_id}"
    res = _request("GET", path, token, None)
    if res.get("code") != 0:
        return None
    data = res.get("data", {})
    document = data.get("document", {}) if isinstance(data, dict) else {}
    return document.get("revision_id")


def _resolve_parent_block_id(token: str, document_id: str) -> str:
    explicit = os.getenv("FEISHU_PARENT_BLOCK_ID")
    if explicit:
        return explicit

    root_id = _get_document_root_block_id(token, document_id)
    if root_id:
        return root_id

    blocks = _get_document_blocks(token, document_id)
    if os.getenv("FEISHU_DEBUG", "0") == "1":
        preview = [
            {
                "block_id": b.get("block_id"),
                "block_type": b.get("block_type"),
                "has_children": b.get("has_children"),
                "parent_id": b.get("parent_id"),
            }
            for b in blocks[:10]
        ]
        print(f"blocks preview: {preview}")
    # Prefer a page-like block or the document's root (block_type == 1).
    for block in blocks:
        if block.get("block_type") == 1:
            return block.get("block_id", document_id)
        if "page" in block or "document" in block:
            return block.get("block_id", document_id)

    if blocks:
        return blocks[0].get("block_id", document_id)
    return document_id


def _create_root_block(token: str, document_id: str) -> str:
    # Create a root heading block under the document root to serve as container.
    root_block_id = _resolve_parent_block_id(token, document_id)
    path = f"/docx/v1/documents/{document_id}/blocks/{root_block_id}/children"
    payload = {
        "children": [
            {
                "block_type": 3,
                "heading1": {
                    "elements": [
                        {"text_run": {"content": "研报内容"}},
                    ]
                },
            }
        ]
    }
    res = _request("POST", path, token, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"创建根块失败: {res}")
    items = res.get("data", {}).get("children", [])
    if items:
        return items[0].get("block_id", document_id)
    return document_id


def _create_descendant_blocks(token: str, document_id: str, blocks: list) -> None:
    import uuid

    parent_block_id = os.getenv("FEISHU_PARENT_BLOCK_ID", document_id)

    # Limit blocks to avoid API issues
    max_blocks = int(os.getenv("FEISHU_MAX_BLOCKS_PER_REQUEST", "100"))
    if len(blocks) > max_blocks:
        print(f"警告：块数量 ({len(blocks)}) 超过限制 ({max_blocks})，将分批上传")
        # For now, just take the first batch
        blocks = blocks[:max_blocks]

    # Generate unique block_ids for all blocks
    descendants = []
    children_ids = []

    for block in blocks:
        block_id = str(uuid.uuid4())
        block_copy = block.copy()
        block_copy["block_id"] = block_id
        block_copy["parent_id"] = ""
        descendants.append(block_copy)
        children_ids.append(block_id)

    payload = {
        "index": 0,
        "children_id": children_ids,
        "descendants": descendants,
    }

    path = f"/docx/v1/documents/{document_id}/blocks/{parent_block_id}/descendant"

    if os.getenv("FEISHU_DEBUG", "0") == "1":
        import json
        print(f"Descendant API: 发送 {len(descendants)} 个块")
        print(f"前2个块的类型: {[d.get('block_type') for d in descendants[:2]]}")

    res = _request("POST", path, token, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"创建嵌套块失败（descendant create）: {res}")


def _create_nested_blocks(token: str, document_id: str, blocks: list, revision_id: int | None = None) -> None:
    # Determine a parent block that supports children.
    parent_block_id = _resolve_parent_block_id(token, document_id)

    if not blocks:
        print("没有可写入的块，跳过写入。")
        return

    # Try batch create API first (works better with wiki documents)
    try:
        print("尝试使用批量创建 API...")
        path = f"/docx/v1/documents/{document_id}/blocks/batch_create"
        # Prepare blocks with parent_id
        blocks_with_parent = []
        for block in blocks:
            block_copy = block.copy()
            block_copy["parent_id"] = parent_block_id
            blocks_with_parent.append(block_copy)

        payload = {"blocks": blocks_with_parent}
        res = _request("POST", path, token, payload)
        if res.get("code") == 0:
            print("批量创建成功")
            return
        else:
            print(f"批量创建失败: {res.get('msg')}, 尝试 descendant API...")
    except RuntimeError as exc:
        print(f"批量创建 API 失败, 尝试 descendant API...")

    # Try descendant API (recommended for wiki documents)
    try:
        print("尝试使用 descendant API...")
        _create_descendant_blocks(token, document_id, blocks)
        print("descendant API 创建成功")
        return
    except RuntimeError as exc:
        error_msg = str(exc)
        print(f"descendant API 失败: {error_msg}")
        if os.getenv("FEISHU_DEBUG", "0") == "1":
            print(f"完整错误: {error_msg}")
        print("尝试 children API...")

    # Fallback to children API
    path = f"/docx/v1/documents/{document_id}/blocks/{parent_block_id}/children"
    revision_id = revision_id or os.getenv("FEISHU_DOCUMENT_REVISION_ID")
    if revision_id:
        path = f"{path}?document_revision_id={revision_id}"

    index = int(os.getenv("FEISHU_CHILDREN_INDEX", "0"))
    # API limits children length; send in batches.
    batch_size = 50
    for i in range(0, len(blocks), batch_size):
        payload = {"index": index, "children": blocks[i : i + batch_size]}
        res = _request("POST", path, token, payload)
        if res.get("code") != 0:
            raise RuntimeError(
                "创建嵌套块失败（可能需要调整 block_type/parent_block_id）: " + str(res)
            )


def _insert_bitable_record(token: str, document_url: str, document_title: str) -> None:
    if _skip_bitable():
        print("已设置 FEISHU_SKIP_BITABLE=1，跳过多维表格插入。")
        return
    node_token = os.getenv("FEISHU_BITABLE_NODE_TOKEN")
    table_id = os.getenv("FEISHU_BITABLE_TABLE_ID")

    if not node_token or not table_id:
        print("未设置 FEISHU_BITABLE_NODE_TOKEN/FEISHU_BITABLE_TABLE_ID，已跳过多维表格插入。")
        print("TODO: 在这里补充表格定位与字段映射。")
        return

    node = _get_wiki_node(token, node_token, "wiki")
    obj_token = node.get("obj_token")
    obj_type = node.get("obj_type")
    if not obj_token:
        raise RuntimeError(f"获取知识空间节点失败，未返回 obj_token: {node}")
    if obj_type and obj_type != "bitable":
        print(f"警告：节点类型为 {obj_type}，但当前流程按多维表格处理。")

    fields = {
        "AI生成标题": document_title,
        "链接": document_url,
        "推送状态": "未推送",
    }
    message_id = os.getenv("FEISHU_MESSAGE_ID", "").strip()
    if message_id:
        fields["message_id"] = message_id
    if "feishu" in document_url:
        fields["AI 读取飞书云文档"] = document_url

    tags = os.getenv("FEISHU_BITABLE_TAGS", "").strip()
    if tags:
        fields["标签"] = [t.strip() for t in tags.split(",") if t.strip()]

    payload = {"fields": fields}
    path = f"/bitable/v1/apps/{obj_token}/tables/{table_id}/records"
    res = _request("POST", path, token, payload)
    if res.get("code") != 0:
        raise RuntimeError(f"插入多维表格失败: {res}")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create Feishu docx from Markdown file")
    parser.add_argument("markdown_file", nargs="?", help="Path to markdown file (optional)")
    args = parser.parse_args()

    _load_dotenv()
    print("开始读取 Markdown 文件...")
    md_path = _find_markdown_file(args.markdown_file)
    print(f"读取文件: {md_path.name}")
    md_text = md_path.read_text(encoding="utf-8")

    document_title = md_path.stem

    print("准备获取访问令牌...")
    token = _get_access_token()
    if os.getenv("FEISHU_USE_OPENAPI", "0") != "1":
        print("使用 lark-cli 创建飞书文档...")
        document_id, document_url = _create_doc_via_lark_cli(md_path, document_title)
    else:
        print("使用飞书 OpenAPI 创建飞书文档...")
        document_id, document_url = _create_doc_via_openapi(token, md_text, document_title)

    _insert_bitable_record(token, document_url, document_title)

    print(f"文档创建完成: {document_id}")
    print(f"文档链接: {document_url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
