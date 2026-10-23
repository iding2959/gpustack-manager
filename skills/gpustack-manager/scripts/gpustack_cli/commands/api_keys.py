"""API key 管理（/v2/api-keys）。

key 是权限边界：列表只回 masked_value，明文只在创建响应里出现一次，
所以 create 必须把明文打给调用方，之后再也拿不到。
"""

import argparse
from typing import Any, Dict, List, Optional

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, fetch_list, get_json, require_ok
from ..output import dump_json, json_list_payload, print_table, sort_items


SCOPES = ["*", "management", "inference"]


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
            item.get("masked_value"),
            item.get("user_name"),
            item.get("created_at"),
            item.get("expires_at") or "-",
        ]
        for item in items
    ]
    print_table(["ID", "名称", "scope", "值", "用户", "创建时间", "过期时间"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个 API key（值不回显，服务端只给 masked_value）。")
    return EXIT_OK


def cmd_api_keys_get(cfg: Config, args: argparse.Namespace) -> int:
    items = sort_items(fetch_list(cfg, "/v2/api-keys")[1])
    target = next((item for item in items if item.get("id") == args.key_id), None)
    if target is None:
        raise UsageError(f"找不到 API key id={args.key_id}")
    if args.json:
        dump_json(target)
        return EXIT_OK
    rows = [
        ["ID", target.get("id")],
        ["名称", target.get("name")],
        ["scope", ",".join(target.get("scope") or [])],
        ["masked_value", target.get("masked_value")],
        ["owner_principal_id", target.get("owner_principal_id")],
        ["允许的模型", ",".join(target.get("allowed_model_names") or []) or "（不限）"],
        ["描述", target.get("description") or "-"],
        ["创建时间", target.get("created_at")],
        ["过期时间", target.get("expires_at") or "-"],
    ]
    print_table(["字段", "值"], rows)
    return EXIT_OK


def build_api_key_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"name": args.name}
    if args.scope:
        payload["scope"] = args.scope
    if args.description:
        payload["description"] = args.description
    if args.expires_in is not None:
        payload["expires_in"] = args.expires_in
    if args.allowed_model_names:
        payload["allowed_model_names"] = args.allowed_model_names
    return payload


def cmd_api_keys_create(cfg: Config, args: argparse.Namespace) -> int:
    payload = build_api_key_payload(args)
    if args.dry_run:
        if args.json:
            dump_json(payload)
        else:
            print("[dry-run] POST /v2/api-keys 请求体（未发送）:")
            dump_json(payload)
        return EXIT_OK
    result = require_ok(_request(cfg, "POST", "/v2/api-keys", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(f"已创建 API key {result.get('name')} (id={result.get('id')})")
    # 明文只有这一次，之后服务端只回 masked_value
    print(f"value: {result.get('value')}")
    print("提示：明文只显示这一次，请立刻保存；列表里只会有 masked_value。")
    return EXIT_OK


def cmd_api_keys_delete(cfg: Config, args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("删除 API key 须显式加 --yes（会让使用该 key 的调用方立刻 401）")
    response = _request(cfg, "DELETE", f"/v2/api-keys/{args.key_id}")
    if args.json:
        dump_json({"id": args.key_id, "http_status": response.status})
    elif response.ok:
        print(f"已删除 API key id={args.key_id}")
    require_ok(response)
    return EXIT_OK


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 api-keys 命令组。"""
    p_keys = add_sub(subparsers, "api-keys", "API 密钥", common)
    keys_sub = p_keys.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(keys_sub, "list", "列出 API key（值不回显）", common)
    p.set_defaults(func=cmd_api_keys_list)

    p = add_sub(keys_sub, "get", "查看单个 API key 详情", common)
    p.add_argument("key_id", metavar="<id>", type=int)
    p.set_defaults(func=cmd_api_keys_get)

    p = add_sub(keys_sub, "create", "创建 API key（明文只在返回值里出现一次）", common)
    p.add_argument("--name", required=True, help="key 名称")
    p.add_argument("--scope", nargs="+", choices=SCOPES, help="权限范围：*/management/inference（可多个）")
    p.add_argument("--description", help="备注")
    p.add_argument("--expires-in", dest="expires_in", type=int, metavar="SECONDS", help="多少秒后过期")
    p.add_argument("--allowed-model-names", dest="allowed_model_names", nargs="+",
                   help="只允许调用这些模型（inference scope 下生效）")
    p.add_argument("--dry-run", action="store_true", help="只打印请求体，不提交")
    p.set_defaults(func=cmd_api_keys_create)

    p = add_sub(keys_sub, "delete", "删除 API key（需 --yes）", common)
    p.add_argument("key_id", metavar="<id>", type=int)
    p.add_argument("--yes", action="store_true", help="确认执行（危险操作）")
    p.set_defaults(func=cmd_api_keys_delete)
