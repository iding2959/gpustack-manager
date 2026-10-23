"""raw 逃生口：任意方法与路径，配合 --data。

190 个接口不可能全部封装，保留穿透能力给高级场景。
"""

import argparse
import json
import sys
from typing import Any, Optional

from .. import EXIT_API, EXIT_OK, UsageError, add_sub
from ..config import Config
from ..http import _request, require_ok
from ..output import dump_json


def cmd_raw(cfg: Config, args: argparse.Namespace) -> int:
    if not args.path.startswith("/"):
        raise UsageError(f"PATH 必须以 / 开头，例如 /v2/models；收到: {args.path}")
    data = parse_data_option(args.data)
    response = _request(cfg, args.method, args.path, data=data)
    if response.ok:
        if args.json:
            payload = response.json()
            dump_json(payload if payload is not None else response.text)
        else:
            sys.stdout.write(response.text)
            if response.text and not response.text.endswith("\n"):
                sys.stdout.write("\n")
        return EXIT_OK
    require_ok(response)
    return EXIT_API


def parse_data_option(value: Optional[str]) -> Any:
    if value is None:
        return None
    raw = value
    if value.startswith("@"):
        path = value[1:]
        try:
            with open(path, "r", encoding="utf-8") as handle:
                raw = handle.read()
        except OSError as exc:
            raise UsageError(f"--data 文件无法读取: {path}: {exc}")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise UsageError(f"--data 需要合法 JSON（或 @文件）: {exc}") from exc


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 raw。"""
    p = add_sub(subparsers, "raw", "逃生口：任意方法与路径", common)
    p.add_argument("method", metavar="METHOD", type=str.upper, choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("path", metavar="PATH", help="必须以 / 开头，如 /v2/models")
    p.add_argument("--data", help="JSON 请求体，或 @文件")
    p.set_defaults(func=cmd_raw)
