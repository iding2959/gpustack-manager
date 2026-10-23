"""模型文件：下载任务与本地目录登记（/v2/model-files）。

管理面关注三件事：文件登记在哪个 worker、下载/校验状态、以及别重复登记
（服务端唯一键是 (local_path, worker_id)，重复直接 409）。
"""

import argparse
from typing import Any, Dict, List, Optional

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, fetch_list, get_json, require_ok
from ..output import (
    dump_json,
    human_bytes,
    json_list_payload,
    pct,
    print_table,
    sort_items,
)
from ..resolve import source_label


# source 决定哪些定位字段是必填的：服务端按 source 做交叉校验，缺了直接 422
REQUIRED_LOCATORS = {
    "local_path": ("local_path", "本地目录路径"),
    "huggingface": ("huggingface_repo_id", "HuggingFace 仓库 ID"),
    "model_scope": ("model_scope_model_id", "ModelScope 模型 ID"),
}


def cmd_model_files_list(cfg: Config, args: argparse.Namespace) -> int:
    params = {"worker_id": getattr(args, "worker_id", None), "search": getattr(args, "search", None)}
    payload, items = fetch_list(cfg, "/v2/model-files", params=params)
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


def cmd_model_files_get(cfg: Config, args: argparse.Namespace) -> int:
    payload = get_json(cfg, f"/v2/model-files/{args.model_file_id}")
    if args.json:
        dump_json(payload)
        return EXIT_OK
    item = payload or {}
    # state_message 是排障主入口（路径不存在、下载失败都写在这里）
    rows = [
        ["ID", item.get("id")],
        ["来源", item.get("source")],
        ["路径/仓库", source_label(item)],
        ["worker", item.get("worker_id")],
        ["状态", item.get("state")],
        ["状态信息", item.get("state_message") or "-"],
        ["进度", pct(item.get("download_progress"))],
        ["大小", human_bytes(item.get("size"))],
        ["是否 LoRA", item.get("is_lora")],
        ["基座模型", item.get("base_model") or "-"],
        ["删除时清理", item.get("cleanup_on_delete")],
        ["创建时间", item.get("created_at")],
    ]
    print_table(["字段", "值"], rows)
    return EXIT_OK


def build_model_file_payload(args: argparse.Namespace) -> Dict[str, Any]:
    source = args.source
    field, label = REQUIRED_LOCATORS[source]
    if not getattr(args, field):
        raise UsageError(f"source={source} 时必须给 --{field.replace('_', '-')}（{label}）")

    payload: Dict[str, Any] = {"source": source}
    for name in ("local_path", "huggingface_repo_id", "huggingface_filename",
                 "model_scope_model_id", "model_scope_file_path"):
        value = getattr(args, name, None)
        if value:
            payload[name] = value
    if args.worker_id is not None:
        payload["worker_id"] = args.worker_id
    if args.cleanup_on_delete:
        payload["cleanup_on_delete"] = True
    if args.is_lora:
        payload["is_lora"] = True
    if args.base_model:
        payload["base_model"] = args.base_model
    return payload


def cmd_model_files_create(cfg: Config, args: argparse.Namespace) -> int:
    payload = build_model_file_payload(args)
    if args.dry_run:
        if args.json:
            dump_json(payload)
        else:
            print("[dry-run] POST /v2/model-files 请求体（未发送）:")
            dump_json(payload)
        return EXIT_OK
    result = require_ok(_request(cfg, "POST", "/v2/model-files", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(
        f"已登记模型文件 id={result.get('id')} worker={result.get('worker_id')} "
        f"state={result.get('state')}"
    )
    print("提示：登记后服务端会在该 worker 上校验路径/统计大小，用 model-files get 看 state_message。")
    return EXIT_OK


def cmd_model_files_delete(cfg: Config, args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("删除模型文件须显式加 --yes（会移出登记表，cleanup_on_delete=true 时还会删文件）")
    response = _request(cfg, "DELETE", f"/v2/model-files/{args.model_file_id}")
    if args.json:
        dump_json({"id": args.model_file_id, "http_status": response.status})
    elif response.ok:
        print(f"已删除模型文件 id={args.model_file_id}")
    require_ok(response)
    return EXIT_OK


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 model-files 命令组。"""
    p_files = add_sub(subparsers, "model-files", "模型文件/下载任务", common)
    files_sub = p_files.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(files_sub, "list", "列出模型文件与下载进度", common)
    p.add_argument("--worker-id", type=int, help="按 worker 过滤")
    p.add_argument("--search", help="按路径/仓库搜索")
    p.set_defaults(func=cmd_model_files_list)

    p = add_sub(files_sub, "get", "查看模型文件详情（含 state_message）", common)
    p.add_argument("model_file_id", metavar="<id>", type=int)
    p.set_defaults(func=cmd_model_files_get)

    p = add_sub(files_sub, "create", "登记模型文件/发起下载（唯一键 local_path+worker_id）", common)
    p.add_argument("--source", required=True, choices=sorted(REQUIRED_LOCATORS), help="来源类型")
    p.add_argument("--local-path", dest="local_path", help="source=local_path：worker 容器内可见的目录")
    p.add_argument("--hf-repo-id", dest="huggingface_repo_id", help="source=huggingface：仓库 ID")
    p.add_argument("--hf-filename", dest="huggingface_filename", help="source=huggingface：可选单文件")
    p.add_argument("--ms-model-id", dest="model_scope_model_id", help="source=model_scope：模型 ID")
    p.add_argument("--ms-file-path", dest="model_scope_file_path", help="source=model_scope：可选单文件")
    p.add_argument("--worker-id", type=int, help="登记到哪个 worker（不传由服务端决定）")
    p.add_argument("--cleanup-on-delete", dest="cleanup_on_delete", action="store_true",
                   help="删除该模型文件时同时删磁盘文件")
    p.add_argument("--is-lora", dest="is_lora", action="store_true", help="标记为 LoRA")
    p.add_argument("--base-model", dest="base_model", help="LoRA 的基座模型")
    p.add_argument("--dry-run", action="store_true", help="只打印请求体，不提交")
    p.set_defaults(func=cmd_model_files_create)

    p = add_sub(files_sub, "delete", "删除模型文件登记（需 --yes）", common)
    p.add_argument("model_file_id", metavar="<id>", type=int)
    p.add_argument("--yes", action="store_true", help="确认执行（危险操作）")
    p.set_defaults(func=cmd_model_files_delete)
