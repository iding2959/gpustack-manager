#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GPUStack 管理 CLI（仅标准库，Python 3.9+）。

这里是入口：解析参数、按优先级装配配置、分发到 gpustack_cli.commands 里的实现并统一
把异常转成退出码（0 成功 / 1 API 或网络错误 / 2 参数错误）。实现按职责拆到 gpustack_cli/，
单文件不超过 500 行，Agent 按需读取更省 token。

常用示例：
    python3 gpustack.py --base-url http://192.168.0.202 --api-key $GPUSTACK_API_KEY health
    python3 gpustack.py models list --state ready
    python3 gpustack.py models start qwen35-9b-local -r 1
    python3 gpustack.py instances list --model qwen35-9b-local
    python3 gpustack.py --json raw GET /v2/models | jq '.items[].name'
"""

import os
import sys
from typing import Optional, Sequence

from gpustack_cli import ApiError, EXIT_API, EXIT_OK, EXIT_USAGE, UsageError
from gpustack_cli.cli import build_parser, fill_defaults, normalize_backend_parameters
from gpustack_cli.config import resolve_config


def force_utf8() -> None:
    """Windows 控制台/重定向时中文可能按 GBK 写失败，这里只做尽力而为的修正。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8")
        except (OSError, ValueError):
            pass


def silence_stdout() -> None:
    """管道对端已关闭：把 stdout 指到 /dev/null，否则解释器退出刷缓冲会再报 BrokenPipe 并改坏退出码。"""
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except (OSError, ValueError):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    force_utf8()
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = fill_defaults(parser.parse_args(normalize_backend_parameters(raw_argv, parser)))
    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        cfg = resolve_config(args)
        return handler(cfg, args) or EXIT_OK
    except UsageError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ApiError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return EXIT_API
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        # 管道给 head/less 时会提前关闭 stdout，这不是错误
        silence_stdout()
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
