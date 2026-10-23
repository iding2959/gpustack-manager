#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPUStack 管理 CLI（仅标准库）。

设计取舍：
- 只用 Python 3.9+ 标准库（urllib / json / argparse），因为 Agent 与运维机器的
  第一诉求是「有 python3 就能跑」，任何 pip 安装都会变成额外失败点；
- 密钥绝不写进代码：参数 > 环境变量 > 配置文件 > 默认值，配置文件权限由用户自理；
- 默认输出人读表格，--json 输出原始 JSON，方便直接管道给 jq；
- 危险操作（删除）必须显式 --yes，避免 Agent 一次手滑毁掉线上模型。

常用示例：
    python3 gpustack.py --base-url http://192.168.0.202 --api-key $GPUSTACK_API_KEY health
    python3 gpustack.py models list --state ready
    python3 gpustack.py models start qwen35-9b-local -r 1
    python3 gpustack.py instances list --model qwen35-9b-local
    python3 gpustack.py --json raw GET /v2/models | jq '.items[].name'
"""

import argparse
import dataclasses
import datetime
import json
import os
import shlex
import ssl
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EXIT_OK = 0
EXIT_API = 1
EXIT_USAGE = 2

DEFAULT_BASE_URL = "http://127.0.0.1"
DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gpustack", "config.json")
DEFAULT_TIMEOUT = 30.0
BODY_SNIPPET = 500
MAX_NAME_LIST = 20
PER_PAGE = 100  # 服务端 perPage=0 会 500，默认 100 会静默截断，所以固定 100 并自己翻页
MAX_PAGES = 50  # 防御性上限：5000 条以内足够运维场景，避免误触发无限翻页
REDIRECT_CODES = (301, 302, 303, 307, 308)
# PUT /v2/models/{id} 会按 source 校验定位字段（实测缺 local_path 直接 422），所以按来源原样带上
SOURCE_LOCATION_FIELDS = {
    "local_path": ("local_path",),
    "huggingface": ("huggingface_repo_id", "huggingface_filename"),
    "model_scope": ("model_scope_model_id", "model_scope_file_path"),
}
WIDE_WIDTH_CHARS = ("W", "F")


# ---------------------------------------------------------------------------
# 错误与配置
# ---------------------------------------------------------------------------


class CliError(Exception):
    """CLI 自身抛出的错误，统一由 main 转成退出码。"""

    exit_code = EXIT_API


class ApiError(CliError):
    """HTTP / 网络 / 数据解析失败。"""

    exit_code = EXIT_API


class UsageError(CliError):
    """命令行参数本身不合法。"""

    exit_code = EXIT_USAGE


@dataclasses.dataclass(frozen=True)
class Config:
    base_url: str
    api_key: Optional[str]
    timeout: float
    insecure: bool


def load_config_file(path: str, explicit: bool) -> Dict[str, Any]:
    """读取配置文件；显式指定的路径不存在时视为参数错误，隐式默认路径缺失则忽略。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        if explicit:
            raise UsageError(f"配置文件不存在: {path}")
        return {}
    except OSError as exc:
        raise UsageError(f"配置文件无法读取: {path}: {exc}")
    except ValueError as exc:
        raise UsageError(f"配置文件不是合法 JSON: {path}: {exc}")
    if not isinstance(data, dict):
        raise UsageError(f"配置文件顶层必须是 JSON 对象: {path}")
    return data


def normalize_base_url(value: str) -> str:
    """容忍用户只写 host:port；末尾斜杠必须去掉，否则拼路径会变成 //v2/...。"""
    url = (value or "").strip()
    if not url:
        return DEFAULT_BASE_URL
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    return url.rstrip("/")


def resolve_config(args: argparse.Namespace) -> Config:
    explicit_config = getattr(args, "config", None)
    config_path = explicit_config or DEFAULT_CONFIG_PATH
    file_values = load_config_file(config_path, explicit=bool(explicit_config))

    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("GPUSTACK_BASE_URL")
        or file_values.get("base_url")
        or DEFAULT_BASE_URL
    )
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("GPUSTACK_API_KEY")
        or file_values.get("api_key")
    )
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    else:
        api_key = None

    timeout = getattr(args, "timeout", DEFAULT_TIMEOUT)
    if timeout is None or timeout <= 0:
        raise UsageError(f"--timeout 必须大于 0，收到: {timeout}")

    return Config(
        base_url=normalize_base_url(base_url),
        api_key=api_key,
        timeout=float(timeout),
        insecure=bool(getattr(args, "insecure", False)),
    )


# ---------------------------------------------------------------------------
# HTTP 层（urllib）
# ---------------------------------------------------------------------------


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """302 打向 Grafana 页面的接口不能跟跳，调用方要读 Location。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_OPENER_CACHE: Dict[Tuple[bool, bool], urllib.request.OpenerDirector] = {}


def _opener(insecure: bool, follow_redirects: bool) -> urllib.request.OpenerDirector:
    """按 (insecure, 是否跟跳) 缓存 opener；urllib 每次请求都新建连接，缓存只省构造开销。"""
    key = (insecure, follow_redirects)
    cached = _OPENER_CACHE.get(key)
    if cached is not None:
        return cached
    handlers: List[Any] = []
    if insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    if not follow_redirects:
        handlers.append(_NoRedirectHandler())
    opener = urllib.request.build_opener(*handlers)
    _OPENER_CACHE[key] = opener
    return opener


@dataclasses.dataclass(frozen=True)
class Response:
    method: str
    path: str
    status: int
    headers: Dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json(self) -> Any:
        text = self.text.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ApiError(
                f"{self.method} {self.path} -> 响应不是合法 JSON: {text[:BODY_SNIPPET]}"
            ) from exc


def _request(
    cfg: Config,
    method: str,
    path: str,
    data: Any = None,
    params: Optional[Dict[str, Any]] = None,
    follow_redirects: bool = True,
) -> Response:
    """发一次请求并把非 2xx 也当响应返回，是否算错误由调用方决定（302 就是合法结果）。"""
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    display = path + (("?" + query) if query else "")
    body = json.dumps(data).encode("utf-8") if data is not None else None
    request = urllib.request.Request(cfg.base_url + display, data=body, method=method.upper())
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if cfg.api_key:
        request.add_header("Authorization", "Bearer " + cfg.api_key)

    method_name = method.upper()
    try:
        with _opener(cfg.insecure, follow_redirects).open(request, timeout=cfg.timeout) as raw:
            status = getattr(raw, "status", None) or getattr(raw, "code", 0)
            headers = dict(raw.headers.items())
            payload = raw.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            payload = exc.read()
        except OSError:
            payload = b""
    except urllib.error.URLError as exc:
        raise ApiError(f"{method_name} {display} -> 网络错误: {exc.reason}") from exc
    except OSError as exc:
        raise ApiError(f"{method_name} {display} -> 网络错误: {exc}") from exc
    return Response(method_name, display, status, headers, payload)


def get_header(response: Response, name: str) -> Optional[str]:
    """HTTP 头大小写不固定（HTTPError 给的是 'Location'），统一按小写比对。"""
    target = name.lower()
    for key, value in response.headers.items():
        if key.lower() == target:
            return value
    return None


def require_ok(response: Response) -> Response:
    if response.ok:
        return response
    snippet = " ".join(response.text.split())[:BODY_SNIPPET] or "(空响应体)"
    hint = ""
    if response.status == 401:
        hint = "（检查 --api-key / $GPUSTACK_API_KEY / 配置文件）"
    elif response.status == 422:
        hint = "（请求参数未通过服务端校验，检查日期格式/必填项）"
    raise ApiError(f"{response.method} {response.path} -> HTTP {response.status}: {snippet}{hint}")


def get_json(cfg: Config, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return require_ok(_request(cfg, "GET", path, params=params)).json()


def items_of(payload: Any, path: str) -> List[Dict[str, Any]]:
    """列表响应有三种形态：{items,pagination}、只有 {items}、裸数组；统一取出 item 列表。"""
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return list(items)
    raise ApiError(
        f"GET {path} -> 响应结构不是列表包裹: {json.dumps(payload, ensure_ascii=False)[:BODY_SNIPPET]}"
    )


def list_items(cfg: Config, path: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """只取一页（用于名称解析这类只需要少量候选的场景）。"""
    query = dict(params or {})
    query.setdefault("perPage", PER_PAGE)
    return items_of(get_json(cfg, path, params=query), path)


def fetch_list(cfg: Config, path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[Any, List[Dict[str, Any]]]:
    """翻页取全量：服务端 perPage=0 会 500、默认 100 会静默截断（total 更大只回 100 条）。

    返回 (合并后的响应体, 全部 item)，这样 --json 输出也完整，表格也不会缺行。
    """
    query = dict(params or {})
    query["page"] = 1
    query["perPage"] = PER_PAGE
    first = get_json(cfg, path, params=query)
    items = items_of(first, path)
    if not isinstance(first, dict):
        return first, items
    pagination = first.get("pagination") or {}
    total_pages = pagination.get("totalPage") or 1
    try:
        total_pages = int(total_pages)
    except (TypeError, ValueError):
        total_pages = 1
    page = 1
    while page < total_pages and page < MAX_PAGES:
        page += 1
        query["page"] = page
        items.extend(items_of(get_json(cfg, path, params=query), path))
    if total_pages > MAX_PAGES:
        print(f"提示：共 {total_pages} 页，只取到前 {MAX_PAGES} 页", file=sys.stderr)
    merged = dict(first)
    merged["items"] = items
    return merged, items


def sort_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """客户端按 id 排序：服务端 sort_by 白名单每个端点不同（-id 直接 422），不能指望它。"""

    def key(item: Dict[str, Any]) -> Tuple[int, int, str]:
        value = item.get("id")
        if isinstance(value, int):
            return (0, value, "")
        text = str(value or "")
        if text.isdigit():
            return (0, int(text), "")
        return (1, 0, text)

    return sorted(items, key=key)


def json_list_payload(payload: Any, items: List[Dict[str, Any]]) -> Any:
    """--json 也输出翻页合并后的全量，保留服务端包裹形态（裸数组仍输出裸数组）。"""
    if isinstance(payload, dict):
        merged = dict(payload)
        merged["items"] = items
        return merged
    return items


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 表格渲染（中文字符按双宽近似）
# ---------------------------------------------------------------------------


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in WIDE_WIDTH_CHARS else 1 for ch in text)


def clip_text(text: str, limit: int) -> str:
    if display_width(text) <= limit:
        return text
    kept: List[str] = []
    width = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in WIDE_WIDTH_CHARS else 1
        if width + char_width > limit - 1:
            break
        kept.append(char)
        width += char_width
    return "".join(kept) + "…"


def format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    max_column_width: int = 52,
) -> str:
    header_cells = [clip_text(str(header), max_column_width) for header in headers]
    body_cells = [
        [clip_text(format_value(cell), max_column_width) for cell in row] for row in rows
    ]
    widths = [display_width(cell) for cell in header_cells]
    for row in body_cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))

    def render(cells: Sequence[str]) -> str:
        padded = [
            cell + " " * (widths[index] - display_width(cell))
            for index, cell in enumerate(cells)
        ]
        return "  ".join(padded).rstrip()

    lines = [render(header_cells), "  ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in body_cells)
    return "\n".join(lines)


def print_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], max_column_width: int = 52) -> None:
    if not rows:
        print("（无数据）")
        return
    print(format_table(headers, rows, max_column_width=max_column_width))


def pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


def human_bytes(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            return f"{int(number)}B" if unit == "B" else f"{number:.1f}{unit}"
        number /= 1024
    return f"{number:.1f}PiB"


def human_duration(seconds: Any) -> str:
    if seconds is None:
        return "-"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and not days:
        parts.append(f"{minutes}分")
    return "".join(parts) or f"{total}秒"


def count_of(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return value


def source_label(item: Dict[str, Any]) -> Optional[str]:
    """模型与 model-file 共用同一套来源字段，统一拼一个可读路径。"""
    source = item.get("source")
    if source == "local_path":
        return item.get("local_path")
    if source == "huggingface":
        repo = item.get("huggingface_repo_id")
        filename = item.get("huggingface_filename")
        if repo and filename:
            return f"{repo}/{filename}"
        return repo
    if source == "model_scope":
        model_id = item.get("model_scope_model_id")
        file_path = item.get("model_scope_file_path")
        if model_id and file_path:
            return f"{model_id}/{file_path}"
        return model_id
    return None


def model_state(model: Dict[str, Any]) -> str:
    """v2.2.3 的模型对象没有 state 字段，只能由副本数推导三态。"""
    replicas = model.get("replicas") or 0
    ready = model.get("ready_replicas") or 0
    if replicas == 0:
        return "stopped"
    if ready >= replicas:
        return "ready"
    return "not_ready"


def names_hint(prefix: str, names: Sequence[str]) -> str:
    if not names:
        return f"（{prefix}：无）"
    return f"（{prefix}：{', '.join(names)}）"


def available_names(cfg: Config, path: str) -> List[str]:
    """解析失败时列出可用名称，让报错本身就能指导下一步。"""
    try:
        items = list_items(cfg, path, params={"perPage": MAX_NAME_LIST})
    except ApiError:
        return []
    names: List[str] = []
    for item in items:
        name = item.get("name")
        if name and name not in names:
            names.append(str(name))
    return names[:MAX_NAME_LIST]


def resolve_model(cfg: Config, reference: str) -> Dict[str, Any]:
    """<name|id> 解析：名字优先。

    `GET /v2/models?name=` 被服务端忽略（照样返回全量），只有 `search` 可用，所以必须
    拉 search 结果在客户端做精确 name 匹配；名字没命中再回退数字 id，这样「名字恰好是数字」
    的模型不会被同号 id 抢走。
    """
    for item in list_items(cfg, "/v2/models", params={"search": reference}):
        if item.get("name") == reference:
            return item
    if reference.isdigit():
        response = _request(cfg, "GET", f"/v2/models/{int(reference)}")
        if response.ok:
            return response.json()
        if response.status != 404:
            require_ok(response)
    raise ApiError(f"找不到模型: {reference}" + names_hint("可用模型", available_names(cfg, "/v2/models")))


def resolve_worker(cfg: Config, reference: str) -> Dict[str, Any]:
    """同 resolve_model：search 精确匹配 name 优先，再回退数字 id。"""
    for item in list_items(cfg, "/v2/workers", params={"search": reference}):
        if item.get("name") == reference:
            return item
    if reference.isdigit():
        response = _request(cfg, "GET", f"/v2/workers/{int(reference)}")
        if response.ok:
            return response.json()
        if response.status != 404:
            require_ok(response)
    raise ApiError(f"找不到 worker: {reference}" + names_hint("可用 worker", available_names(cfg, "/v2/workers")))


def resolve_route(cfg: Config, reference: str) -> Dict[str, Any]:
    """route 也走 search + 客户端精确匹配（name/effective_name），再回退数字 id。"""
    for item in list_items(cfg, "/v2/model-routes", params={"search": reference}):
        if reference in (item.get("name"), item.get("effective_name")):
            return item
    if reference.isdigit():
        response = _request(cfg, "GET", f"/v2/model-routes/{int(reference)}")
        if response.ok:
            return response.json()
        if response.status != 404:
            require_ok(response)
    raise ApiError(
        f"找不到模型路由: {reference}" + names_hint("可用路由", available_names(cfg, "/v2/model-routes"))
    )


def resolve_cluster_id(cfg: Config, explicit: Optional[int]) -> int:
    """POST /v2/models 不带 cluster_id 会 500（NotNullViolation），所以必须兜底。"""
    if explicit:
        return explicit
    clusters = list_items(cfg, "/v2/clusters")
    for cluster in clusters:
        if cluster.get("is_default"):
            return int(cluster["id"])
    if len(clusters) == 1:
        return int(clusters[0]["id"])
    raise ApiError("无法确定 cluster_id：没有 is_default 集群且集群数不为 1，请用 --cluster-id 指定")


def parse_json_option(value: Optional[str], option: str) -> Optional[Any]:
    if value is None:
        return None
    try:
        return json.loads(value)
    except ValueError as exc:
        raise UsageError(f"{option} 需要合法 JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# 只读命令
# ---------------------------------------------------------------------------


def cmd_health(cfg: Config, args: argparse.Namespace) -> int:
    probes = (("healthz", "/healthz"), ("readyz", "/readyz"), ("version", "/version"))
    payload: Dict[str, Any] = {}
    rows: List[List[Any]] = []
    failed: List[str] = []
    for name, path in probes:
        response = _request(cfg, "GET", path)
        raw = response.text.strip()
        if response.ok:
            try:
                payload[name] = json.loads(raw) if raw else None
            except ValueError:
                payload[name] = raw
        else:
            payload[name] = {"http_status": response.status, "body": raw[:BODY_SNIPPET]}
            failed.append(name)
        rows.append([name, response.status, summarize_probe(payload[name])])

    if args.json:
        dump_json(payload)
    else:
        print(format_table(["端点", "HTTP", "结果"], rows))
        if not failed:
            print("\n服务正常。")
    if failed:
        print(f"探针失败: {', '.join(failed)}", file=sys.stderr)
        return EXIT_API
    return EXIT_OK


def summarize_probe(value: Any) -> str:
    if isinstance(value, dict):
        if "http_status" in value:
            return f"HTTP {value['http_status']}"
        version = value.get("version")
        commit = value.get("git_commit")
        if version and commit:
            return f"{version} (git {commit[:7]})"
        return json.dumps(value, ensure_ascii=False)[:80]
    return str(value)


def cmd_models_list(cfg: Config, args: argparse.Namespace) -> int:
    params = {"state": args.state, "search": args.search, "cluster_id": args.cluster_id}
    payload, items = fetch_list(cfg, "/v2/models", params=params)
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            model.get("id"),
            model.get("name"),
            f"{model.get('ready_replicas', 0)}/{model.get('replicas', 0)}",
            model.get("backend"),
            model.get("source"),
            model_state(model),
        ]
        for model in items
    ]
    print_table(["ID", "名称", "副本(ready/total)", "后端", "来源", "状态"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个模型。")
    return EXIT_OK


def cmd_models_get(cfg: Config, args: argparse.Namespace) -> int:
    model = resolve_model(cfg, args.model)
    if args.json:
        dump_json(model)
        return EXIT_OK
    rows = [
        ["ID", model.get("id")],
        ["名称", model.get("name")],
        ["状态", model_state(model)],
        ["副本(ready/total)", f"{model.get('ready_replicas', 0)}/{model.get('replicas', 0)}"],
        ["后端", join_two(model.get("backend"), model.get("backend_version"))],
        ["来源", model.get("source")],
        ["路径", source_label(model)],
        ["集群", model.get("cluster_id")],
        ["放置策略", model.get("placement_strategy")],
        ["访问策略", model.get("access_policy")],
        ["描述", model.get("description")],
        ["创建时间", model.get("created_at")],
        ["更新时间", model.get("updated_at")],
    ]
    print(format_table(["字段", "值"], rows))
    return EXIT_OK


def join_two(first: Any, second: Any) -> str:
    values = [str(value) for value in (first, second) if value]
    return " ".join(values) or "-"


def model_replicas_payload(model: Dict[str, Any], replicas: int) -> Dict[str, Any]:
    """构造 PUT /v2/models/{id} 的最小可写字段集。

    契约里「只提交 source+name+replicas」是错的：服务端 Pydantic 会按 source 校验定位字段
    （实测 local_path 缺失 → 422 `local_path must be provided when source is 'local_path'`），
    所以按 source 原样带上定位字段；但绝不能回写只读字段（id/created_at/updated_at/
    ready_replicas/state 等），那才是旧实现的真隐患。
    """
    payload: Dict[str, Any] = {
        "source": model.get("source"),
        "name": model.get("name"),
        "replicas": replicas,
    }
    for field in SOURCE_LOCATION_FIELDS.get(model.get("source"), ()):
        value = model.get(field)
        if value not in (None, ""):
            payload[field] = value
    return payload


def set_replicas(cfg: Config, args: argparse.Namespace, replicas: int) -> int:
    model = resolve_model(cfg, args.model)
    body = model_replicas_payload(model, replicas)
    if args.dry_run:
        if args.json:
            dump_json(body)
        else:
            print(f"[dry-run] PUT /v2/models/{model['id']} 请求体（未发送）:")
            dump_json(body)
        return EXIT_OK
    result = require_ok(_request(cfg, "PUT", f"/v2/models/{model['id']}", data=body)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(
        f"模型 {model.get('name')} (id={model.get('id')}) replicas: "
        f"{model.get('replicas', 0)} -> {result.get('replicas', replicas)}"
    )
    print("提示：状态变化是异步的，用 models get 或 instances list 观察实例重建。")
    return EXIT_OK


def cmd_models_start(cfg: Config, args: argparse.Namespace) -> int:
    return set_replicas(cfg, args, args.replicas)


def cmd_models_stop(cfg: Config, args: argparse.Namespace) -> int:
    return set_replicas(cfg, args, 0)


def cmd_models_scale(cfg: Config, args: argparse.Namespace) -> int:
    return set_replicas(cfg, args, args.replicas)


def build_model_payload(cfg: Config, args: argparse.Namespace) -> Dict[str, Any]:
    if args.source == "local_path" and not args.local_path:
        raise UsageError("--source local_path 必须配 --local-path（worker 容器内可见路径）")
    if args.source == "huggingface" and not args.hf_repo_id:
        raise UsageError("--source huggingface 必须配 --hf-repo-id")
    if args.source == "model_scope" and not args.ms_model_id:
        raise UsageError("--source model_scope 必须配 --ms-model-id")

    payload: Dict[str, Any] = {
        "name": args.name,
        "source": args.source,
        "cluster_id": resolve_cluster_id(cfg, args.cluster_id),
        "replicas": args.replicas,
        "placement_strategy": args.placement_strategy,
    }
    source_fields = {
        "local_path": ("local_path", args.local_path),
        "huggingface": ("huggingface_repo_id", args.hf_repo_id),
        "model_scope": ("model_scope_model_id", args.ms_model_id),
    }
    field, value = source_fields[args.source]
    payload[field] = value

    if args.source == "huggingface" and args.hf_filename:
        payload["huggingface_filename"] = args.hf_filename
    if args.source == "model_scope" and args.ms_file_path:
        payload["model_scope_file_path"] = args.ms_file_path

    if args.backend:
        payload["backend"] = args.backend
    if args.backend_version:
        payload["backend_version"] = args.backend_version
    if args.image_name:
        payload["image_name"] = args.image_name
    if args.description:
        payload["description"] = args.description
    if args.gpu_selector:
        payload["gpu_selector"] = parse_json_option(args.gpu_selector, "--gpu-selector")
    if args.worker_selector:
        payload["worker_selector"] = parse_json_option(args.worker_selector, "--worker-selector")
    if args.backend_parameter:
        payload["backend_parameters"] = parse_backend_parameters(args.backend_parameter)
    if args.cpu_offloading is not None:
        payload["cpu_offloading"] = args.cpu_offloading
    if args.distributed_inference:
        payload["distributed_inference_across_workers"] = True
    if args.env:
        payload["env"] = parse_env_pairs(args.env)
    return payload


def parse_backend_parameters(values: Sequence[str]) -> List[str]:
    """后端参数是「一个元素一个 token」的数组；含引号的值必须拆成 flag 与 value 两项，
    否则服务端拼命令行时 shlex 会把引号吃掉。"""
    tokens: List[str] = []
    for value in values:
        try:
            tokens.extend(shlex.split(value))
        except ValueError as exc:
            raise UsageError(f"--backend-parameter 无法解析: {value}: {exc}") from exc
    return tokens


def parse_env_pairs(values: Sequence[str]) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for value in values:
        key, sep, rest = value.partition("=")
        if not sep or not key:
            raise UsageError(f"--env 需要 KEY=VALUE 形式，收到: {value}")
        env[key] = rest
    return env


def cmd_models_create(cfg: Config, args: argparse.Namespace) -> int:
    payload = build_model_payload(cfg, args)
    if args.dry_run:
        if args.json:
            dump_json(payload)
        else:
            print("[dry-run] POST /v2/models 请求体（未发送）:")
            dump_json(payload)
        return EXIT_OK
    result = require_ok(_request(cfg, "POST", "/v2/models", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(f"已创建模型 {result.get('name')} (id={result.get('id')}, replicas={result.get('replicas')})")
    print("提示：实例创建是异步的，用 instances list --model 跟踪状态。")
    return EXIT_OK


def cmd_models_delete(cfg: Config, args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("删除模型须显式加 --yes（危险操作，防止误删线上模型）")
    model = resolve_model(cfg, args.model)
    response = _request(cfg, "DELETE", f"/v2/models/{model['id']}")
    if args.json:
        dump_json({"deleted": model.get("name"), "id": model.get("id"), "http_status": response.status})
    elif response.ok:
        print(f"已删除模型 {model.get('name')} (id={model.get('id')})")
    require_ok(response)
    return EXIT_OK


def cmd_instances_list(cfg: Config, args: argparse.Namespace) -> int:
    model_id = None
    if args.model:
        model_id = resolve_model(cfg, args.model).get("id")
    params = {"state": args.state, "model_id": model_id}
    payload, items = fetch_list(cfg, "/v2/model-instances", params=params)
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("model_name"),
            item.get("state"),
            item.get("worker_name"),
            item.get("worker_ip") or item.get("worker_advertise_address"),
            item.get("port"),
            item.get("pid"),
        ]
        for item in items
    ]
    print_table(["ID", "模型", "状态", "worker", "IP", "端口", "PID"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个实例。")
    return EXIT_OK


def cmd_instances_logs(cfg: Config, args: argparse.Namespace) -> int:
    if not str(args.instance).isdigit():
        raise UsageError(f"实例 ID 必须是数字，收到: {args.instance}")
    path = f"/v2/model-instances/{int(args.instance)}/logs"
    params = {"tail": args.tail}
    if args.follow:
        params["follow"] = True
        return stream_text(cfg, path, params)
    response = require_ok(_request(cfg, "GET", path, params=params))
    sys.stdout.write(response.text)
    if response.text and not response.text.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


def stream_text(cfg: Config, path: str, params: Dict[str, Any]) -> int:
    """--follow 必须边读边写，不能等响应结束（日志不会结束）。"""
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    url = cfg.base_url + path + (("?" + query) if query else "")
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "text/plain, application/octet-stream, */*")
    if cfg.api_key:
        request.add_header("Authorization", "Bearer " + cfg.api_key)
    try:
        raw = _opener(cfg.insecure, True).open(request, timeout=cfg.timeout)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:BODY_SNIPPET]
        raise ApiError(f"GET {path} -> HTTP {exc.code}: {body}") from exc
    except OSError as exc:
        raise ApiError(f"GET {path} -> 网络错误: {exc}") from exc

    with raw:
        # read1 只取「当前已到达」的数据；read(n) 会死等凑满 n 字节，日志流会永远卡住
        read_chunk = getattr(raw, "read1", None) or raw.read
        try:
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        except KeyboardInterrupt:
            print("\n（已停止跟踪）", file=sys.stderr)
        except BrokenPipeError:
            raise
        except OSError as exc:
            raise ApiError(f"GET {path} -> 读取日志中断: {exc}") from exc
    return EXIT_OK


def cmd_workers_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/workers", params={"cluster_id": args.cluster_id})
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = []
    for worker in items:
        status = worker.get("status") or {}
        memory = status.get("memory") or {}
        gpus = status.get("gpu_devices") or []
        rows.append(
            [
                worker.get("id"),
                worker.get("name"),
                worker.get("ip"),
                worker.get("state"),
                pct((status.get("cpu") or {}).get("utilization_rate")),
                f"{human_bytes(memory.get('used'))}/{human_bytes(memory.get('total'))}",
                len(gpus),
            ]
        )
    # 内存列用 used（驱动实际占用）：allocated 是调度器 claim，空载节点上恒为 0，列里没信息量
    print_table(["ID", "名称", "IP", "状态", "CPU%", "内存 used/total", "GPU 数"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个 worker。")
    return EXIT_OK


def cmd_workers_get(cfg: Config, args: argparse.Namespace) -> int:
    worker = resolve_worker(cfg, args.worker)
    if args.json:
        dump_json(worker)
        return EXIT_OK
    status = worker.get("status") or {}
    memory = status.get("memory") or {}
    gpus = status.get("gpu_devices") or []
    os_info = status.get("os") or {}
    kernel = status.get("kernel") or {}
    uptime = status.get("uptime") or {}
    maintenance = worker.get("maintenance") or {}
    rows = [
        ["ID", worker.get("id")],
        ["名称", worker.get("name")],
        ["主机名", worker.get("hostname")],
        ["IP", worker.get("ip")],
        ["状态", worker.get("state")],
        ["维护模式", "开启" if maintenance.get("enabled") else "关闭"],
        ["CPU%", pct((status.get("cpu") or {}).get("utilization_rate"))],
        ["内存 used/total", f"{human_bytes(memory.get('used'))}/{human_bytes(memory.get('total'))}"],
        ["内存 allocated", human_bytes(memory.get("allocated"))],
        ["GPU 数", len(gpus)],
        ["GPU 型号", ", ".join(str(gpu.get("name")) for gpu in gpus) or "-"],
        ["操作系统", join_two(os_info.get("name"), os_info.get("version"))],
        ["内核", join_two(kernel.get("name"), kernel.get("release"))],
        ["运行时长", human_duration(uptime.get("uptime"))],
        ["状态信息", worker.get("state_message")],
    ]
    print(format_table(["字段", "值"], rows))
    return EXIT_OK


def cmd_workers_maintenance(cfg: Config, args: argparse.Namespace) -> int:
    worker = resolve_worker(cfg, args.worker)
    enabled = bool(args.enable)
    maintenance: Dict[str, Any] = {"enabled": enabled}
    if args.message:
        maintenance["message"] = args.message
    result = require_ok(
        _request(cfg, "PUT", f"/v2/workers/{worker['id']}", data={"maintenance": maintenance})
    ).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    state = (result.get("maintenance") or {}).get("enabled")
    print(f"worker {worker.get('name')} (id={worker.get('id')}) 维护模式: {'开启' if state else '关闭'}")
    if enabled:
        print("提示：维护模式会优雅拉出该节点上的模型实例，调度器不再向它派发新实例。")
    return EXIT_OK


def cmd_workers_dashboard(cfg: Config, args: argparse.Namespace) -> int:
    worker = resolve_worker(cfg, args.worker)
    response = _request(cfg, "GET", f"/v2/workers/{worker['id']}/dashboard", follow_redirects=False)
    if response.status in REDIRECT_CODES:
        location = get_header(response, "Location") or "(无 Location)"
        if args.json:
            dump_json({"worker": worker.get("name"), "http_status": response.status, "dashboard": location})
        else:
            print(f"Grafana 面板（HTTP {response.status} 重定向，用浏览器打开）：\n  {location}")
        return EXIT_OK
    payload = require_ok(response).json()
    if args.json:
        dump_json(payload)
        return EXIT_OK
    print(format_table(["字段", "值"], [[key, summarize_probe(value)] for key, value in (payload or {}).items()]))
    return EXIT_OK


def cmd_gpus_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/gpu-devices", params={"cluster_id": args.cluster_id})
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = []
    for device in items:
        memory = device.get("memory") or {}
        rows.append(
            [
                device.get("worker_name"),
                device.get("index"),
                device.get("name"),
                f"{human_bytes(memory.get('used'))}/{human_bytes(memory.get('total'))}",
                human_bytes(memory.get("allocated")),
                pct(memory.get("utilization_rate")),
            ]
        )
    print_table(["worker", "序号", "型号", "显存 used/total", "已分配", "利用率"], rows)
    if rows:
        print(f"\n共 {len(rows)} 张 GPU。")
    return EXIT_OK


def cmd_model_files_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/model-files")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("source"),
            source_label(item),
            item.get("state"),
            pct(item.get("download_progress")),
            human_bytes(item.get("size")),
            item.get("worker_id"),
        ]
        for item in items
    ]
    print_table(["ID", "来源", "名称", "状态", "进度", "大小", "worker"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个模型文件。")
    return EXIT_OK


def cmd_clusters_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/clusters")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            cluster.get("id"),
            cluster.get("name"),
            cluster.get("provider"),
            cluster.get("state"),
            cluster.get("workers"),
            cluster.get("ready_workers"),
            cluster.get("gpus"),
            cluster.get("models"),
            "是" if cluster.get("is_default") else "否",
        ]
        for cluster in items
    ]
    print_table(["ID", "名称", "provider", "状态", "worker", "ready", "GPU", "模型", "默认"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个集群。")
    return EXIT_OK


def cmd_dashboard(cfg: Config, args: argparse.Namespace) -> int:
    payload = get_json(cfg, "/v2/dashboard")
    if args.json:
        dump_json(payload)
        return EXIT_OK
    counts = (payload or {}).get("resource_counts") or {}
    load = ((payload or {}).get("system_load") or {}).get("current") or {}
    print("资源统计")
    print(
        format_table(
            ["资源", "数量"],
            [
                ["集群", counts.get("cluster_count")],
                ["worker", counts.get("worker_count")],
                ["GPU", counts.get("gpu_count")],
                ["模型", counts.get("model_count")],
                ["实例", counts.get("model_instance_count")],
            ],
        )
    )
    print("\n当前负载")
    print(
        format_table(
            ["指标", "当前"],
            [
                ["CPU", pct(load.get("cpu"))],
                ["内存", pct(load.get("ram"))],
                ["GPU", pct(load.get("gpu"))],
                ["显存", pct(load.get("vram"))],
            ],
        )
    )
    return EXIT_OK


def cmd_usage_summary(cfg: Config, args: argparse.Namespace) -> int:
    today = datetime.date.today()
    start_date = args.start_date or (today - datetime.timedelta(days=30)).isoformat()
    end_date = args.end_date or today.isoformat()
    payload = get_json(
        cfg, "/v2/usage/summary", params={"start_date": start_date, "end_date": end_date}
    )
    if args.json:
        dump_json(payload)
        return EXIT_OK
    labels = (
        ("total_tokens", "总 token"),
        ("input_tokens", "输入 token"),
        ("output_tokens", "输出 token"),
        ("token_active_users", "token 活跃用户"),
        ("gpu_hours", "GPU 小时"),
        ("instance_hours", "实例小时"),
        ("storage_gb_days", "存储 GB·天"),
        ("active_users", "活跃用户"),
    )
    data = payload or {}
    rows = [[label, data.get(key)] for key, label in labels]
    print(f"统计区间: {start_date} ~ {end_date}")
    print(format_table(["指标", "值"], rows))
    return EXIT_OK


def cmd_api_keys_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/api-keys")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("name"),
            ",".join(item.get("scope") or []),
            item.get("masked_value") or ("自定义 key" if item.get("is_custom") else "-"),
            item.get("user_name"),
            item.get("created_at"),
        ]
        for item in items
    ]
    print_table(["ID", "名称", "scope", "值", "用户", "创建时间"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个 API key（值不回显，服务端只给 masked_value）。")
    return EXIT_OK


def cmd_backends_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/inference-backends")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("backend_name"),
            item.get("default_version"),
            "是" if item.get("is_built_in") else "否",
            "启用" if item.get("enabled") else "停用",
            item.get("description"),
        ]
        for item in items
    ]
    print_table(["ID", "后端", "默认版本", "内置", "状态", "描述"], rows)
    if rows:
        print("\n提示：models create --backend 传这里的后端显示名（如 vLLM / SGLang / MindIE / VoxBox）。")
    return EXIT_OK


def cmd_routes_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/model-routes")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("effective_name") or item.get("name"),
            count_of(item.get("targets")),
            count_of(item.get("ready_targets")),
            item.get("access_policy"),
            item.get("description"),
            item.get("created_at"),
        ]
        for item in items
    ]
    print_table(["ID", "名称", "targets", "ready_targets", "访问策略", "描述", "创建时间"], rows)
    if rows:
        print(f"\n共 {len(rows)} 条路由。")
        print("提示：v2.2.3 的 route 只回 targets/ready_targets 计数，target 明细由 add-targets 接口维护。")
    return EXIT_OK


def cmd_routes_get(cfg: Config, args: argparse.Namespace) -> int:
    route = resolve_route(cfg, args.route)
    detail = require_ok(_request(cfg, "GET", f"/v2/model-routes/{route['id']}")).json() or route
    if args.json:
        dump_json(detail)
        return EXIT_OK
    rows = [
        ["ID", detail.get("id")],
        ["名称", detail.get("name")],
        ["effective_name", detail.get("effective_name")],
        ["targets", count_of(detail.get("targets"))],
        ["ready_targets", count_of(detail.get("ready_targets"))],
        ["访问策略", detail.get("access_policy")],
        ["描述", detail.get("description")],
        ["generic_proxy", detail.get("generic_proxy")],
        ["创建时间", detail.get("created_at")],
        ["更新时间", detail.get("updated_at")],
    ]
    print(format_table(["字段", "值"], rows))
    print("\n提示：v2.2.3 只回 targets/ready_targets 计数（target 明细不对外暴露）。")
    return EXIT_OK


def cmd_routes_create(cfg: Config, args: argparse.Namespace) -> int:
    model = resolve_model(cfg, args.model)
    payload: Dict[str, Any] = {
        "name": args.name,
        "targets": [{"model_id": model.get("id"), "weight": args.weight}],
    }
    if args.description:
        payload["description"] = args.description
    if args.dry_run:
        if args.json:
            dump_json(payload)
        else:
            print("[dry-run] POST /v2/model-routes 请求体（未发送）:")
            dump_json(payload)
        return EXIT_OK
    result = require_ok(_request(cfg, "POST", "/v2/model-routes", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(
        f"已创建模型路由 {result.get('name')} "
        f"(id={result.get('id')}, targets={count_of(result.get('targets'))})"
    )
    print("提示：v2.2.3 的 /v1 推理要靠 route 才路由得到模型，建好后 /v1/models 才会出现该模型。")
    return EXIT_OK


def cmd_routes_delete(cfg: Config, args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("删除模型路由须显式加 --yes（危险操作，防止误删线上路由）")
    route = resolve_route(cfg, args.route)
    response = _request(cfg, "DELETE", f"/v2/model-routes/{route['id']}")
    if args.json:
        dump_json(
            {"deleted": route.get("name"), "id": route.get("id"), "http_status": response.status}
        )
    elif response.ok:
        print(f"已删除模型路由 {route.get('name')} (id={route.get('id')})")
    require_ok(response)
    return EXIT_OK


def cmd_benchmarks_list(cfg: Config, args: argparse.Namespace) -> int:
    payload, items = fetch_list(cfg, "/v2/benchmarks")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("name"),
            item.get("model_name"),
            item.get("state"),
            pct(item.get("progress")),
            item.get("dataset_name"),
            item.get("created_at"),
        ]
        for item in items
    ]
    print_table(["ID", "名称", "模型", "状态", "进度", "数据集", "创建时间"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个压测任务。")
    return EXIT_OK


# ---------------------------------------------------------------------------
# 推理面命令
# ---------------------------------------------------------------------------


def chat_payload(args: argparse.Namespace) -> Dict[str, Any]:
    messages: List[Dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})
    payload: Dict[str, Any] = {
        "model": args.model,
        "messages": messages,
        "stream": bool(args.stream and not args.json),
    }
    if args.temperature is not None:
        payload["temperature"] = args.temperature
    if args.max_tokens is not None:
        payload["max_tokens"] = args.max_tokens
    return payload


def cmd_chat(cfg: Config, args: argparse.Namespace) -> int:
    payload = chat_payload(args)
    if payload["stream"]:
        return stream_chat(cfg, payload)
    result = require_ok(_request(cfg, "POST", "/v1/chat/completions", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    choices = result.get("choices") or []
    message = (choices[0] or {}).get("message") or {}
    print(message.get("content") or "(空回复)")
    usage = result.get("usage") or {}
    if usage:
        print(
            f"\ntokens: prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}"
        )
    return EXIT_OK


def stream_chat(cfg: Config, payload: Dict[str, Any]) -> int:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(cfg.base_url + "/v1/chat/completions", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    if cfg.api_key:
        request.add_header("Authorization", "Bearer " + cfg.api_key)
    try:
        raw = _opener(cfg.insecure, True).open(request, timeout=cfg.timeout)
    except urllib.error.HTTPError as exc:
        snippet = exc.read().decode("utf-8", errors="replace")[:BODY_SNIPPET]
        raise ApiError(f"POST /v1/chat/completions -> HTTP {exc.code}: {snippet}") from exc
    except OSError as exc:
        raise ApiError(f"POST /v1/chat/completions -> 网络错误: {exc}") from exc

    with raw:
        try:
            for line in raw:
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    sys.stdout.write(content)
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        except BrokenPipeError:
            raise
        except OSError as exc:
            raise ApiError(f"POST /v1/chat/completions -> 流式读取中断: {exc}") from exc
    print()
    return EXIT_OK


def cmd_embeddings(cfg: Config, args: argparse.Namespace) -> int:
    payload = {"model": args.model, "input": args.input}
    result = require_ok(_request(cfg, "POST", "/v1/embeddings", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    data = result.get("data") or []
    if not data:
        print(result)
        return EXIT_OK
    vector = data[0].get("embedding") or []
    preview = ", ".join(f"{value:.4f}" for value in vector[:8])
    usage = result.get("usage") or {}
    print(
        format_table(
            ["字段", "值"],
            [
                ["模型", result.get("model") or args.model],
                ["向量维度", len(vector)],
                ["前 8 维", preview],
                ["usage", json.dumps(usage, ensure_ascii=False)],
            ],
        )
    )
    return EXIT_OK


def cmd_raw(cfg: Config, args: argparse.Namespace) -> int:
    if not args.path.startswith("/"):
        raise UsageError(f"PATH 必须以 / 开头，例如 /v2/models；收到: {args.path}")
    data = parse_data_option(args.data)
    response = _request(cfg, args.method, args.path, data=data)
    if response.ok:
        if args.json:
            payload = response.json()
            dump_json(payload if payload is not None else response.text)
        else:
            sys.stdout.write(response.text)
            if response.text and not response.text.endswith("\n"):
                sys.stdout.write("\n")
        return EXIT_OK
    require_ok(response)
    return EXIT_API


def parse_data_option(value: Optional[str]) -> Any:
    if value is None:
        return None
    raw = value
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise UsageError(f"--data 文件无法读取: {path}: {exc}")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise UsageError(f"--data 需要合法 JSON（或 @文件）: {exc}") from exc


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def known_option_strings(parser: argparse.ArgumentParser) -> set:
    """收集所有层级的已知选项，用来判断 --backend-parameter 后面的 token 是不是新选项。"""
    options = set(getattr(parser, "_option_string_actions", {}))
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                if isinstance(subparser, argparse.ArgumentParser):
                    options |= known_option_strings(subparser)
    return options


def normalize_backend_parameters(argv: Sequence[str], parser: argparse.ArgumentParser) -> List[str]:
    """让 `--backend-parameter --tensor-parallel-size 2` 这种不加引号的写法也能用。

    argparse 会把以 - 开头的值当成选项、报「expected one argument」，但后端参数天然
    就是「--flag value」形态。这里以「已知选项」为界，把连续 token 合回一个值。
    """
    if "--backend-parameter" not in argv:
        return list(argv)
    known = known_option_strings(parser)
    normalized: List[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--backend-parameter":
            values: List[str] = []
            index += 1
            while index < len(argv) and not (
                argv[index].startswith("-") and argv[index].partition("=")[0] in known
            ):
                values.append(argv[index])
                index += 1
            # 没跟值就原样交给 argparse 报错，避免把错误吞掉
            normalized.append(
                "--backend-parameter=" + " ".join(values) if values else "--backend-parameter"
            )
            continue
        normalized.append(token)
        index += 1
    return normalized


def build_common_parser() -> argparse.ArgumentParser:
    """全局参数用 SUPPRESS 默认值：这样「子命令前」的值不会被「子命令后」的默认值覆盖。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        metavar="URL",
        default=argparse.SUPPRESS,
        help=f"GPUStack 地址；优先级 参数 > $GPUSTACK_BASE_URL > 配置文件 > {DEFAULT_BASE_URL}",
    )
    common.add_argument(
        "--api-key",
        metavar="KEY",
        default=argparse.SUPPRESS,
        help="API key；优先级 参数 > $GPUSTACK_API_KEY > 配置文件（不要写进脚本）",
    )
    common.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}，内容 {{\"base_url\":...,\"api_key\":...}}",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出原始 JSON（保证可被 jq 解析）",
    )
    common.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=f"请求超时秒数，默认 {int(DEFAULT_TIMEOUT)}",
    )
    common.add_argument(
        "--insecure",
        action="store_true",
        default=argparse.SUPPRESS,
        help="关闭 TLS 证书校验（自签证书实例）",
    )
    return common


def add_sub(
    subparsers: Any, name: str, help_text: str, common: argparse.ArgumentParser
) -> argparse.ArgumentParser:
    return subparsers.add_parser(
        name, help=help_text, description=help_text, parents=[common]
    )


def build_parser() -> argparse.ArgumentParser:
    common = build_common_parser()
    epilog = """示例:
  gpustack.py --base-url http://192.168.0.202 --api-key $GPUSTACK_API_KEY health
  gpustack.py models list --state ready
  gpustack.py models get qwen35-9b-local
  gpustack.py models start qwen35-9b-local -r 1        # PUT 带 source 对应定位字段（见 models start --help）
  gpustack.py models stop qwen35-9b-local
  gpustack.py models create --name qwen35 --source local_path --local-path /mnt/models/Qwen3.5-9B --backend vLLM
  gpustack.py instances list --model qwen35-9b-local
  gpustack.py instances logs 3 --tail 200
  gpustack.py usage summary --start-date 2026-10-01 --end-date 2026-10-23
  gpustack.py routes create --name qwen35-9b-local --model 2   # /v1 推理前提
  gpustack.py --json raw GET /v2/models | jq '.items[].name'
"""
    parser = argparse.ArgumentParser(
        prog="gpustack.py",
        description="GPUStack 管理 CLI（v2 API，仅标准库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=epilog,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<命令>")

    p_health = add_sub(subparsers, "health", "健康检查（/healthz /readyz /version）", common)
    p_health.set_defaults(func=cmd_health)

    # models
    p_models = add_sub(subparsers, "models", "模型管理", common)
    models_sub = p_models.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(models_sub, "list", "列出模型", common)
    p.add_argument("--state", choices=["ready", "not_ready", "stopped"], help="按状态过滤")
    p.add_argument("--search", help="按名称搜索")
    p.add_argument("--cluster-id", type=int, help="按集群过滤")
    p.set_defaults(func=cmd_models_list)

    p = add_sub(models_sub, "get", "查看模型详情（名称或 ID）", common)
    p.add_argument("model", metavar="<name|id>")
    p.set_defaults(func=cmd_models_get)

    p = add_sub(models_sub, "start", "启动模型（PUT replicas=N，按 source 自动带定位字段）", common)
    p.add_argument("model", metavar="<name|id>")
    p.add_argument("-r", "--replicas", type=int, default=1, help="目标副本数，默认 1")
    p.add_argument("--dry-run", action="store_true", help="只打印将要提交的请求体，不真正修改")
    p.set_defaults(func=cmd_models_start)

    p = add_sub(models_sub, "stop", "停止模型（PUT replicas=0，按 source 自动带定位字段）", common)
    p.add_argument("model", metavar="<name|id>")
    p.add_argument("--dry-run", action="store_true", help="只打印将要提交的请求体，不真正修改")
    p.set_defaults(func=cmd_models_stop)

    p = add_sub(models_sub, "scale", "按副本数伸缩（同 start）", common)
    p.add_argument("model", metavar="<name|id>")
    p.add_argument("-r", "--replicas", type=int, required=True, help="目标副本数")
    p.add_argument("--dry-run", action="store_true", help="只打印将要提交的请求体，不真正修改")
    p.set_defaults(func=cmd_models_scale)

    p = add_sub(models_sub, "create", "创建模型（POST /v2/models）", common)
    p.add_argument("--name", required=True, help="模型名称（必填）")
    p.add_argument(
        "--source",
        required=True,
        choices=["huggingface", "model_scope", "local_path"],
        help="模型来源（v2.2.3 没有 ollama_library）",
    )
    p.add_argument("--hf-repo-id", dest="hf_repo_id", help="HuggingFace repo id")
    p.add_argument("--hf-filename", dest="hf_filename", help="HuggingFace 单文件路径")
    p.add_argument("--ms-model-id", dest="ms_model_id", help="ModelScope 模型 id")
    p.add_argument("--ms-file-path", dest="ms_file_path", help="ModelScope 文件路径")
    p.add_argument("--local-path", dest="local_path", help="本地路径（必须 worker 容器内可见）")
    p.add_argument("--backend", help="后端显示名，如 vLLM / SGLang / MindIE / VoxBox")
    p.add_argument("--backend-version", dest="backend_version", help="后端版本，如 0.25.1")
    p.add_argument("--image-name", dest="image_name", help="自定义运行时镜像")
    p.add_argument("--replicas", type=int, default=1, help="副本数，默认 1")
    p.add_argument("--gpu-selector", dest="gpu_selector", help="GPU 选择器 JSON，如 '{\"cuda\": 1}'")
    p.add_argument("--worker-selector", dest="worker_selector", help="worker 选择器 JSON")
    p.add_argument(
        "--backend-parameter",
        dest="backend_parameter",
        action="append",
        metavar="'--flag value'",
        help="后端参数，可重复；一次给 flag 与值（内部按 shell 词法拆成多元素）",
    )
    p.add_argument(
        "--placement-strategy",
        dest="placement_strategy",
        choices=["spread", "binpack"],
        default="spread",
        help="放置策略，默认 spread",
    )
    p.add_argument("--cluster-id", dest="cluster_id", type=int, help="集群 ID；默认取 is_default 集群")
    p.add_argument(
        "--cpu-offloading",
        dest="cpu_offloading",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="启用/禁用 CPU offloading",
    )
    p.add_argument(
        "--distributed-inference",
        dest="distributed_inference",
        action="store_true",
        help="跨 worker 分布式推理（distributed_inference_across_workers=true）",
    )
    p.add_argument("--description", help="模型描述")
    p.add_argument("--env", action="append", metavar="KEY=VALUE", help="环境变量，可重复")
    p.add_argument("--dry-run", action="store_true", help="只打印将要提交的请求体，不真正创建")
    p.set_defaults(func=cmd_models_create)

    p = add_sub(models_sub, "delete", "删除模型（必须 --yes）", common)
    p.add_argument("model", metavar="<name|id>")
    p.add_argument("--yes", action="store_true", help="确认删除（缺失则直接拒绝）")
    p.set_defaults(func=cmd_models_delete)

    # instances
    p_instances = add_sub(subparsers, "instances", "模型实例管理", common)
    instances_sub = p_instances.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(instances_sub, "list", "列出模型实例", common)
    p.add_argument("--model", help="按模型名/ID 过滤（内部解析成 model_id）")
    p.add_argument("--state", help="按实例状态过滤，如 running/pending")
    p.set_defaults(func=cmd_instances_list)

    p = add_sub(instances_sub, "logs", "打印实例日志（--follow 持续跟踪）", common)
    p.add_argument("instance", metavar="<id>", help="实例 ID")
    p.add_argument("--tail", type=int, default=200, help="末尾行数，默认 200")
    p.add_argument("--follow", action="store_true", help="持续跟踪（Ctrl-C 退出）")
    p.set_defaults(func=cmd_instances_logs)

    # workers
    p_workers = add_sub(subparsers, "workers", "worker 管理", common)
    workers_sub = p_workers.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(workers_sub, "list", "列出 worker", common)
    p.add_argument("--cluster-id", type=int, help="按集群过滤")
    p.set_defaults(func=cmd_workers_list)

    p = add_sub(workers_sub, "get", "查看 worker 详情", common)
    p.add_argument("worker", metavar="<id|name>")
    p.set_defaults(func=cmd_workers_get)

    p = add_sub(workers_sub, "maintenance", "维护模式开关（优雅拉出跑模型的节点）", common)
    p.add_argument("worker", metavar="<id|name>")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--enable", action="store_true", help="进入维护模式")
    group.add_argument("--disable", action="store_true", help="退出维护模式")
    p.add_argument("--message", help="维护原因说明")
    p.set_defaults(func=cmd_workers_maintenance)

    p = add_sub(workers_sub, "dashboard", "worker 的 Grafana 面板地址（302 重定向）", common)
    p.add_argument("worker", metavar="<id|name>")
    p.set_defaults(func=cmd_workers_dashboard)

    # gpus
    p_gpus = add_sub(subparsers, "gpus", "GPU 设备", common)
    gpus_sub = p_gpus.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(gpus_sub, "list", "列出 GPU 设备", common)
    p.add_argument("--cluster-id", type=int, help="按集群过滤")
    p.set_defaults(func=cmd_gpus_list)

    # model-files
    p_files = add_sub(subparsers, "model-files", "模型文件/下载任务", common)
    files_sub = p_files.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(files_sub, "list", "列出模型文件与下载进度", common)
    p.set_defaults(func=cmd_model_files_list)

    # clusters
    p_clusters = add_sub(subparsers, "clusters", "集群", common)
    clusters_sub = p_clusters.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(clusters_sub, "list", "列出集群", common)
    p.set_defaults(func=cmd_clusters_list)

    p = add_sub(subparsers, "dashboard", "集群概览（资源统计 + 当前负载）", common)
    p.set_defaults(func=cmd_dashboard)

    # usage
    p_usage = add_sub(subparsers, "usage", "用量统计", common)
    usage_sub = p_usage.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(usage_sub, "summary", "用量汇总（服务端要求 start_date/end_date，缺省取最近 30 天）", common)
    p.add_argument("--start-date", dest="start_date", metavar="YYYY-MM-DD", help="起始日期")
    p.add_argument("--end-date", dest="end_date", metavar="YYYY-MM-DD", help="结束日期")
    p.set_defaults(func=cmd_usage_summary)

    # api-keys
    p_keys = add_sub(subparsers, "api-keys", "API 密钥", common)
    keys_sub = p_keys.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(keys_sub, "list", "列出 API key（值不回显）", common)
    p.set_defaults(func=cmd_api_keys_list)

    # benchmarks
    p_bench = add_sub(subparsers, "benchmarks", "压测任务", common)
    bench_sub = p_bench.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(bench_sub, "list", "列出压测任务", common)
    p.set_defaults(func=cmd_benchmarks_list)

    # backends
    p_backends = add_sub(subparsers, "backends", "推理后端配置", common)
    backends_sub = p_backends.add_subparsers(dest="action", metavar="<操作>", required=True)
    p = add_sub(backends_sub, "list", "列出推理后端", common)
    p.set_defaults(func=cmd_backends_list)

    # routes
    p_routes = add_sub(subparsers, "routes", "模型路由（v2.2.3 的 /v1 推理必须建 route）", common)
    routes_sub = p_routes.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(routes_sub, "list", "列出模型路由", common)
    p.set_defaults(func=cmd_routes_list)

    p = add_sub(routes_sub, "get", "查看模型路由详情（名称或 ID）", common)
    p.add_argument("route", metavar="<name|id>")
    p.set_defaults(func=cmd_routes_get)

    p = add_sub(routes_sub, "create", "创建模型路由（让 /v1 能路由到模型）", common)
    p.add_argument("--name", required=True, help="路由名（对外的模型名）")
    p.add_argument("--model", required=True, help="目标模型名或 ID")
    p.add_argument("--weight", type=int, default=1, help="目标权重，默认 1")
    p.add_argument("--description", help="路由描述")
    p.add_argument("--dry-run", action="store_true", help="只打印将要提交的请求体，不真正创建")
    p.set_defaults(func=cmd_routes_create)

    p = add_sub(routes_sub, "delete", "删除模型路由（必须 --yes）", common)
    p.add_argument("route", metavar="<name|id>")
    p.add_argument("--yes", action="store_true", help="确认删除（缺失则直接拒绝）")
    p.set_defaults(func=cmd_routes_delete)

    # 推理面
    p = add_sub(subparsers, "chat", "OpenAI 兼容 chat 冒烟测试（/v1/chat/completions）", common)
    p.add_argument("--model", required=True, help="模型名")
    p.add_argument("--prompt", required=True, help="用户消息")
    p.add_argument("--system", help="system 消息")
    p.add_argument("--temperature", type=float, help="采样温度")
    p.add_argument("--max-tokens", dest="max_tokens", type=int, help="最大生成 token 数")
    p.add_argument("--stream", action="store_true", help="流式输出（与 --json 同时用时以 --json 优先）")
    p.set_defaults(func=cmd_chat)

    p = add_sub(subparsers, "embeddings", "OpenAI 兼容 embeddings（/v1/embeddings）", common)
    p.add_argument("--model", required=True, help="模型名")
    p.add_argument("--input", required=True, help="输入文本")
    p.set_defaults(func=cmd_embeddings)

    p = add_sub(subparsers, "raw", "逃生口：任意方法与路径", common)
    p.add_argument("method", metavar="METHOD", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("path", metavar="PATH", help="必须以 / 开头，如 /v2/models")
    p.add_argument("--data", help="JSON 请求体，或 @文件")
    p.set_defaults(func=cmd_raw)

    return parser


def fill_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """SUPPRESS 默认值让「命令行没写」保持未设置，这里统一补齐，后面的代码都好写。"""
    defaults = {
        "json": False,
        "base_url": None,
        "api_key": None,
        "config": None,
        "timeout": DEFAULT_TIMEOUT,
        "insecure": False,
    }
    for name, default in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    return args


def force_utf8() -> None:
    """Windows 控制台/重定向时中文可能按 GBK 写失败，这里只做尽力而为的修正。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def silence_stdout() -> None:
    """管道对端已关闭：把 stdout 指到 /dev/null，否则解释器退出刷缓冲会再报 BrokenPipe 并改坏退出码。"""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8()
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = fill_defaults(parser.parse_args(normalize_backend_parameters(raw_argv, parser)))
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        cfg = resolve_config(args)
        return handler(cfg, args) or EXIT_OK
    except UsageError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ApiError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return EXIT_API
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # 管道给 head/less 时会提前关闭 stdout，这不是错误
        silence_stdout()
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
