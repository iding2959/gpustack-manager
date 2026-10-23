"""资源与健康类命令：health / gpus / model-files / clusters / dashboard / usage /
api-keys / backends / benchmarks。

这些命令都是「看」为主，共用同一套列表翻页、表格与单位换算逻辑。
"""

import argparse
import datetime
import json
import sys
from typing import Any, Dict, List

from .. import EXIT_API, EXIT_OK, add_sub
from ..config import Config
from ..http import BODY_SNIPPET, _request, fetch_list, get_json
from ..output import (
    dump_json,
    format_table,
    human_bytes,
    json_list_payload,
    pct,
    print_table,
    sort_items,
    summarize_probe,
)
from ..resolve import source_label


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


def cmd_my_models(cfg: Config, args: argparse.Namespace) -> int:
    """/v2/my-models 是「当前 key 能看到/能调用」的模型入口（ModelRoute/ModelAccess）。

    /v2/models 按 principal 归属过滤，多用户实例上用别人的 key 会全空；此时靠这个命令
    与 dashboard 的聚合计数还原真实部署情况。
    """
    payload, items = fetch_list(cfg, "/v2/my-models")
    items = sort_items(items)
    if args.json:
        dump_json(json_list_payload(payload, items))
        return EXIT_OK
    rows = [
        [
            item.get("id"),
            item.get("name"),
            item.get("targets"),
            item.get("ready_targets"),
            item.get("access_policy"),
            item.get("owner_principal_id"),
        ]
        for item in items
    ]
    print_table(["ID", "名称", "targets", "ready", "访问策略", "owner"], rows)
    if rows:
        print(f"\n共 {len(rows)} 个模型入口（ready 是就绪目标数，0 表示目标模型没起来）。")
    return EXIT_OK


def cmd_dashboard(cfg: Config, args: argparse.Namespace) -> int:
    # 多集群实例上不带 cluster_id 会拿到全 0（服务端按「当前集群」算，而当前集群可能不是有负载的那个）
    payload = get_json(cfg, "/v2/dashboard", params={"cluster_id": getattr(args, "cluster_id", None)})
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


def register_health(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 health（必须最先注册，保持 --help 里的顺序）。"""
    p_health = add_sub(subparsers, "health", "健康检查（/healthz /readyz /version）", common)
    p_health.set_defaults(func=cmd_health)


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 gpus/model-files/clusters/dashboard/usage/api-keys/benchmarks/backends。"""
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

    p = add_sub(subparsers, "my-models", "当前 key 可见的模型入口（/v1 能调用的那一批）", common)
    p.set_defaults(func=cmd_my_models)

    p = add_sub(subparsers, "dashboard", "集群概览（资源统计 + 当前负载）", common)
    p.add_argument("--cluster-id", type=int, help="按集群统计（多集群实例必须给，否则服务端按当前集群返回全 0）")
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
