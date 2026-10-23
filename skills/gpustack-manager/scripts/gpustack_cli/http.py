"""HTTP 层：urllib 封装、统一错误格式、分页与三形态列表响应。

只用标准库，是为了让 `python3 gpustack.py` 在任何装了 python3 的机器上零依赖可跑。
"""

import dataclasses
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import ApiError
from .config import Config


BODY_SNIPPET = 500


PER_PAGE = 100  # 服务端 perPage=0 会 500，默认 100 会静默截断，所以固定 100 并自己翻页
MAX_PAGES = 50  # 防御性上限：5000 条以内足够运维场景，避免误触发无限翻页


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
