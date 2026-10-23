"""压测任务（/v2/benchmarks）。

注意：create 会真的在目标实例上跑压测（产生负载），不是"登记"语义；
需要先有运行中的实例（required: name + model_instance_name）。
"""

import argparse
from typing import Any, Dict, List, Optional

from .. import EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, fetch_list, get_json, require_ok
from ..output import dump_json, json_list_payload, pct, print_table, sort_items


STATE_CHOICES = ["pending", "queued", "running", "completed", "stopped", "error", "unreachable"]


def cmd_benchmarks_list(cfg: Config, args: argparse.Namespace) -> int:
    params = {"model_id": getattr(args, "model_id", None), "state": getattr(args, "state", None)}
    payload, items = fetch_list(cfg, "/v2/benchmarks", params=params)
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("name"),
            item.get("model_name"),
            item.get("model_instance_name"),
            item.get("state"),
            pct(item.get("progress")),
            item.get("created_at"),
        ]
        for item in items
    ]
    print_table(["ID", "名称", "模型", "实例", "状态", "进度", "创建时间"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个压测任务。")
    return EXIT_OK


def cmd_benchmarks_get(cfg: Config, args: argparse.Namespace) -> int:
    item = get_json(cfg, f"/v2/benchmarks/{args.benchmark_id}") or {}
    if args.json:
        dump_json(item)
        return EXIT_OK
    rows = [
        ["ID", item.get("id")],
        ["名称", item.get("name")],
        ["模型", item.get("model_name")],
        ["实例", item.get("model_instance_name")],
        ["状态", item.get("state")],
        ["状态信息", item.get("state_message") or "-"],
        ["进度", pct(item.get("progress"))],
        ["profile", item.get("profile")],
        ["总请求数", item.get("total_requests")],
        ["请求速率", item.get("request_rate")],
        ["创建时间", item.get("created_at")],
    ]
    print_table(["字段", "值"], rows)
    return EXIT_OK


def cmd_benchmarks_logs(cfg: Config, args: argparse.Namespace) -> int:
    # 日志是 text/plain 流，直接落 stdout，方便 | tail / | grep
    params = {"tail": args.tail, "follow": "true" if args.follow else None}
    from ..http import _request as request
    response = request(cfg, "GET", f"/v2/benchmarks/{args.benchmark_id}/logs", params=params)
    require_ok(response)
    print(response.text)
    return EXIT_OK


def build_benchmark_payload(args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": args.name,
        "model_instance_name": args.model_instance_name,
    }
    for field in ("description", "profile", "dataset_name", "dataset_input_tokens",
                  "dataset_output_tokens", "dataset_seed", "total_requests", "request_rate"):
        value = getattr(args, field, None)
        if value is not None:
            payload[field] = value
    return payload


def cmd_benchmarks_create(cfg: Config, args: argparse.Namespace) -> int:
    payload = build_benchmark_payload(args)
    if args.dry_run:
        if args.json:
            dump_json(payload)
        else:
            print("[dry-run] POST /v2/benchmarks 请求体（未发送）:")
            dump_json(payload)
        return EXIT_OK
    result = require_ok(_request(cfg, "POST", "/v2/benchmarks", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    print(f"已创建压测任务 {result.get('name')} (id={result.get('id')}, state={result.get('state')})")
    print("提示：压测会真的对目标实例打流量；用 benchmarks logs 看进度，停止用 benchmarks state --state stopped。")
    return EXIT_OK


def cmd_benchmarks_state(cfg: Config, args: argparse.Namespace) -> int:
    if args.state == "stopped" and not args.yes:
        raise UsageError("停止压测须显式加 --yes")
    response = _request(cfg, "PATCH", f"/v2/benchmarks/{args.benchmark_id}/state", data={"state": args.state})
    require_ok(response)
    if args.json:
        dump_json(response.json())
    else:
        print(f"压测任务 {args.benchmark_id} 状态已改为 {args.state}")
    return EXIT_OK


def cmd_benchmarks_delete(cfg: Config, args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("删除压测任务须显式加 --yes")
    response = _request(cfg, "DELETE", f"/v2/benchmarks/{args.benchmark_id}")
    if args.json:
        dump_json({"id": args.benchmark_id, "http_status": response.status})
    elif response.ok:
        print(f"已删除压测任务 id={args.benchmark_id}")
    require_ok(response)
    return EXIT_OK


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 benchmarks 命令组。"""
    p_bench = add_sub(subparsers, "benchmarks", "压测任务", common)
    bench_sub = p_bench.add_subparsers(dest="action", metavar="<操作>", required=True)

    p = add_sub(bench_sub, "list", "列出压测任务", common)
    p.add_argument("--model-id", dest="model_id", type=int, help="按模型过滤")
    p.add_argument("--state", choices=STATE_CHOICES, help="按状态过滤")
    p.set_defaults(func=cmd_benchmarks_list)

    p = add_sub(bench_sub, "get", "查看压测任务详情", common)
    p.add_argument("benchmark_id", metavar="<id>", type=int)
    p.set_defaults(func=cmd_benchmarks_get)

    p = add_sub(bench_sub, "logs", "查看压测日志（文本流）", common)
    p.add_argument("benchmark_id", metavar="<id>", type=int)
    p.add_argument("--tail", type=int, help="只取末尾若干块")
    p.add_argument("--follow", action="store_true", help="持续跟随（Ctrl-C 退出）")
    p.set_defaults(func=cmd_benchmarks_logs)

    p = add_sub(bench_sub, "create", "创建压测任务（会真的打流量）", common)
    p.add_argument("--name", required=True, help="任务名")
    p.add_argument("--model-instance-name", dest="model_instance_name", required=True,
                   help="目标实例名（instances list 里的 name 字段）")
    p.add_argument("--profile", help="压测 profile，默认 Custom")
    p.add_argument("--dataset-name", dest="dataset_name", help="数据集名")
    p.add_argument("--dataset-input-tokens", dest="dataset_input_tokens", type=int)
    p.add_argument("--dataset-output-tokens", dest="dataset_output_tokens", type=int)
    p.add_argument("--dataset-seed", dest="dataset_seed", type=int)
    p.add_argument("--total-requests", dest="total_requests", type=int)
    p.add_argument("--request-rate", dest="request_rate", type=float)
    p.add_argument("--description", help="备注")
    p.add_argument("--dry-run", action="store_true", help="只打印请求体，不提交")
    p.set_defaults(func=cmd_benchmarks_create)

    p = add_sub(bench_sub, "state", "改压测状态（stopped 需 --yes）", common)
    p.add_argument("benchmark_id", metavar="<id>", type=int)
    p.add_argument("--state", required=True, choices=STATE_CHOICES)
    p.add_argument("--yes", action="store_true", help="停止压测时确认")
    p.set_defaults(func=cmd_benchmarks_state)

    p = add_sub(bench_sub, "delete", "删除压测任务（需 --yes）", common)
    p.add_argument("benchmark_id", metavar="<id>", type=int)
    p.add_argument("--yes", action="store_true", help="确认执行")
    p.set_defaults(func=cmd_benchmarks_delete)
