"""GPUStack 管理 CLI 内部包。

单文件膨胀会让 Agent 每次读文件都烧 token，所以按职责拆成 config/http/output/resolve/cli
与 commands/*。包根只放跨模块共用的退出码、异常与 argparse 小工具：add_sub 放在这里是为了
避免 cli.py ↔ commands/*.py 互相 import 形成循环依赖。
"""

import argparse
from typing import Any


EXIT_OK = 0
EXIT_API = 1
EXIT_USAGE = 2


class CliError(Exception):
    """CLI 自身抛出的错误，统一由 main 转成退出码。"""

    exit_code = EXIT_API


class ApiError(CliError):
    """HTTP / 网络 / 数据解析失败。"""

    exit_code = EXIT_API


class UsageError(CliError):
    """命令行参数本身不合法。"""

    exit_code = EXIT_USAGE


def add_sub(
    subparsers: Any, name: str, help_text: str, common: argparse.ArgumentParser
) -> argparse.ArgumentParser:
    return subparsers.add_parser(
        name, help=help_text, description=help_text, parents=[common]
    )
