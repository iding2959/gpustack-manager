"""routes 子命令：模型路由。

v2.2.3 的 /v1 推理必须建 route，否则 /v1/models 为空、chat 返回 404 Model not found；
route 公共 schema 只回 targets/ready_targets 计数，没有 target 明细接口。
"""

import argparse
from typing import Any, Dict

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, fetch_list, require_ok
from ..output import (
    count_of,
    dump_json,
    format_table,
    json_list_payload,
    print_table,
    sort_items,
)
from ..resolve import resolve_model, resolve_route


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


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 routes 命令组。"""
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
