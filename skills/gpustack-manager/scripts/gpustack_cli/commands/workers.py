"""workers 子命令：列表/详情/维护模式/Grafana 面板。

worker 的 status 可能为 null（只剩 state），渲染前一律判空；dashboard 接口是 302 跳 Grafana。
"""

import argparse
from typing import Any, Dict

from .. import EXIT_OK, add_sub
from ..config import Config
from ..http import _request, fetch_list, get_header, require_ok
from ..resolve import resolve_worker
from ..output import (
    dump_json,
    format_table,
    human_bytes,
    human_duration,
    join_two,
    json_list_payload,
    pct,
    print_table,
    sort_items,
    summarize_probe,
)

REDIRECT_CODES = (301, 302, 303, 307, 308)


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


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 workers 命令组。"""
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
