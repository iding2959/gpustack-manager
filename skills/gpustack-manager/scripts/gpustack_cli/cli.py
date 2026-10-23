"""argparse 组装与分发骨架。

build_parser 只负责「按顺序调用各命令模块的 register()」，具体参数定义留在各命令模块，
避免 cli.py 变成新的巨石文件。
"""

import argparse
from typing import List, Sequence

from .commands import inference, instances, models, raw, resources, routes, workers
from .config import DEFAULT_BASE_URL, DEFAULT_CONFIG_PATH, DEFAULT_TIMEOUT


def known_option_strings(parser: argparse.ArgumentParser) -> set:
    """收集所有层级的已知选项，用来判断 --backend-parameter 后面的 token 是不是新选项。"""
    options = set(getattr(parser, "_option_string_actions", {}))
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for subparser in choices.values():
                if isinstance(subparser, argparse.ArgumentParser):
                    options |= known_option_strings(subparser)
    return options


def normalize_backend_parameters(argv: Sequence[str], parser: argparse.ArgumentParser) -> List[str]:
    """让 `--backend-parameter --tensor-parallel-size 2` 这种不加引号的写法也能用。

    argparse 会把以 - 开头的值当成选项、报「expected one argument」，但后端参数天然
    就是「--flag value」形态。这里以「已知选项」为界，把连续 token 合回一个值。
    """
    if "--backend-parameter" not in argv:
        return list(argv)
    known = known_option_strings(parser)
    normalized: List[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--backend-parameter":
            values: List[str] = []
            index += 1
            while index < len(argv) and not (
                argv[index].startswith("-") and argv[index].partition("=")[0] in known
            ):
                values.append(argv[index])
                index += 1
            # 没跟值就原样交给 argparse 报错，避免把错误吞掉
            normalized.append(
                "--backend-parameter=" + " ".join(values) if values else "--backend-parameter"
            )
            continue
        normalized.append(token)
        index += 1
    return normalized


def build_common_parser() -> argparse.ArgumentParser:
    """全局参数用 SUPPRESS 默认值：这样「子命令前」的值不会被「子命令后」的默认值覆盖。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-url",
        metavar="URL",
        default=argparse.SUPPRESS,
        help=f"GPUStack 地址；优先级 参数 > $GPUSTACK_BASE_URL > 配置文件 > {DEFAULT_BASE_URL}",
    )
    common.add_argument(
        "--api-key",
        metavar="KEY",
        default=argparse.SUPPRESS,
        help="API key；优先级 参数 > $GPUSTACK_API_KEY > 配置文件（不要写进脚本）",
    )
    common.add_argument(
        "--username",
        metavar="USER",
        default=argparse.SUPPRESS,
        help="账号密码登录的用户名（与 --password 同用；优先级 参数 > $GPUSTACK_USERNAME > 配置文件）",
    )
    common.add_argument(
        "--password",
        metavar="PASS",
        default=argparse.SUPPRESS,
        help="账号密码登录的密码（不落盘、不写日志；也可用 $GPUSTACK_PASSWORD）",
    )
    common.add_argument(
        "--config",
        metavar="PATH",
        default=argparse.SUPPRESS,
        help=f"配置文件路径，默认 {DEFAULT_CONFIG_PATH}，内容 {{\"base_url\":...,\"api_key\":...}}",
    )
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="输出原始 JSON（保证可被 jq 解析）",
    )
    common.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        default=argparse.SUPPRESS,
        help=f"请求超时秒数，默认 {int(DEFAULT_TIMEOUT)}",
    )
    common.add_argument(
        "--insecure",
        action="store_true",
        default=argparse.SUPPRESS,
        help="关闭 TLS 证书校验（自签证书实例）",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    """组装全部子命令。

    每个命令模块自带 register()，cli 只维护注册顺序（顺序影响 --help 输出）与公共参数，
    这样单文件能压到 500 行以内。
    """
    common = build_common_parser()
    epilog = """示例:
  gpustack.py --base-url http://192.168.0.202 --api-key $GPUSTACK_API_KEY health
  gpustack.py models list --state ready
  gpustack.py models get qwen35-9b-local
  gpustack.py models start qwen35-9b-local -r 1        # PUT 带 source 对应定位字段（见 models start --help）
  gpustack.py models stop qwen35-9b-local
  gpustack.py models create --name qwen35 --source local_path --local-path /mnt/models/Qwen3.5-9B --backend vLLM
  gpustack.py instances list --model qwen35-9b-local
  gpustack.py instances logs 3 --tail 200
  gpustack.py usage summary --start-date 2026-10-01 --end-date 2026-10-23
  gpustack.py routes create --name qwen35-9b-local --model 2   # /v1 推理前提
  gpustack.py --json raw GET /v2/models | jq '.items[].name'
"""
    parser = argparse.ArgumentParser(
        prog="gpustack.py",
        description="GPUStack 管理 CLI（v2 API，仅标准库）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        epilog=epilog,
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<命令>")

    resources.register_health(subparsers, common)
    models.register(subparsers, common)
    instances.register(subparsers, common)
    workers.register(subparsers, common)
    resources.register(subparsers, common)
    routes.register(subparsers, common)
    inference.register(subparsers, common)
    raw.register(subparsers, common)

    return parser


def fill_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """SUPPRESS 默认值让「命令行没写」保持未设置，这里统一补齐，后面的代码都好写。"""
    defaults = {
        "json": False,
        "base_url": None,
        "api_key": None,
        "config": None,
        "timeout": DEFAULT_TIMEOUT,
        "insecure": False,
        "username": None,
        "password": None,
    }
    for name, default in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    return args
