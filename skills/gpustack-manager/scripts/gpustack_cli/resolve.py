"""名称 → ID 解析与模型字段语义。

`GET /v2/models?name=` 被服务端忽略，所以只能 `search` + 客户端精确匹配 name，
未命中再回退数字 id；错误里带上可用名称，让报错本身能指导下一步。
"""

import json
from typing import Any, Dict, List, Optional, Sequence

from . import ApiError, UsageError
from .config import Config
from .http import _request, list_items, require_ok


MAX_NAME_LIST = 20


def model_state(model: Dict[str, Any]) -> str:
    """v2.2.3 的模型对象没有 state 字段，只能由副本数推导三态。"""
    replicas = model.get("replicas") or 0
    ready = model.get("ready_replicas") or 0
    if replicas == 0:
        return "stopped"
    if ready >= replicas:
        return "ready"
    return "not_ready"


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
