"""models 子命令：列表/详情/启停/伸缩/创建/删除。

PUT 必须带 source 对应的定位字段（实测缺 local_path 直接 422），但绝不能回写只读字段。
"""

import argparse
import shlex
from typing import Any, Dict, List, Sequence

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, fetch_list, require_ok
from ..output import (
    dump_json,
    format_table,
    join_two,
    json_list_payload,
    print_table,
    sort_items,
)
from ..resolve import (
    model_state,
    parse_json_option,
    resolve_cluster_id,
    resolve_model,
    source_label,
)


# PUT /v2/models/{id} 会按 source 校验定位字段（实测缺 local_path 直接 422），所以按来源原样带上
SOURCE_LOCATION_FIELDS = {
    "local_path": ("local_path",),
    "huggingface": ("huggingface_repo_id", "huggingface_filename"),
    "model_scope": ("model_scope_model_id", "model_scope_file_path"),
}


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
    elif cfg.username:
        # 会话能看到全量，空就是真的空，别再误导成权限问题
        print("提示：该实例当前没有模型。")
    else:
        # API key 的列表按 principal 归属过滤：非 owner 的 key 会全空而集群聚合仍有数据
        print(
            "提示：API key 的 /v2/models 按 principal 归属过滤，可能看不到别人的模型。"
            "改用 `--username/--password` 账号密码登录可见全量；或临时用 "
            "`dashboard --cluster-id <id>` / `my-models` 交叉验证。"
        )
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


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 models 命令组。"""
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
