"""instances 子命令：实例列表与日志流。

--follow 必须边读边写（read1），否则 read(n) 会等凑满 n 字节、日志永远卡住。
"""

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

from .. import EXIT_OK, ApiError, UsageError, add_sub
from ..config import Config
from ..http import BODY_SNIPPET, _opener, _request, fetch_list, require_ok
from ..output import dump_json, json_list_payload, print_table, sort_items
from ..resolve import resolve_model


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


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 instances 命令组。"""
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
