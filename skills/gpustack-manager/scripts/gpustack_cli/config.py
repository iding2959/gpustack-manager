"""配置来源与优先级：命令行参数 > 环境变量 > 配置文件 > 默认值。

密钥只从这三处读，绝不写进代码：脚本要能公开发布，硬编码 key 等于泄漏。
"""

import argparse
import dataclasses
import json
import os
from typing import Any, Dict, Optional

from . import UsageError


DEFAULT_BASE_URL = "http://127.0.0.1"
DEFAULT_CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".gpustack", "config.json")
DEFAULT_TIMEOUT = 30.0


@dataclasses.dataclass(frozen=True)
class Config:
    base_url: str
    api_key: Optional[str]
    timeout: float
    insecure: bool
    # 账号密码登录（Web 会话）：API key 在部分实例上会被 owner principal 过滤，
    # 而 admin 的会话能看到全量，所以两条鉴权通道都要留
    username: Optional[str] = None
    password: Optional[str] = None


def load_config_file(path: str, explicit: bool) -> Dict[str, Any]:
    """读取配置文件；显式指定的路径不存在时视为参数错误，隐式默认路径缺失则忽略。"""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        if explicit:
            raise UsageError(f"配置文件不存在: {path}")
        return {}
    except OSError as exc:
        raise UsageError(f"配置文件无法读取: {path}: {exc}")
    except ValueError as exc:
        raise UsageError(f"配置文件不是合法 JSON: {path}: {exc}")
    if not isinstance(data, dict):
        raise UsageError(f"配置文件顶层必须是 JSON 对象: {path}")
    return data


def normalize_base_url(value: str) -> str:
    """容忍用户只写 host:port；末尾斜杠必须去掉，否则拼路径会变成 //v2/...。"""
    url = (value or "").strip()
    if not url:
        return DEFAULT_BASE_URL
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "http://" + url
    return url.rstrip("/")


def resolve_config(args: argparse.Namespace) -> Config:
    explicit_config = getattr(args, "config", None)
    config_path = explicit_config or DEFAULT_CONFIG_PATH
    file_values = load_config_file(config_path, explicit=bool(explicit_config))

    base_url = (
        getattr(args, "base_url", None)
        or os.environ.get("GPUSTACK_BASE_URL")
        or file_values.get("base_url")
        or DEFAULT_BASE_URL
    )
    api_key = (
        getattr(args, "api_key", None)
        or os.environ.get("GPUSTACK_API_KEY")
        or file_values.get("api_key")
    )
    if isinstance(api_key, str):
        api_key = api_key.strip() or None
    else:
        api_key = None

    username = (
        getattr(args, "username", None)
        or os.environ.get("GPUSTACK_USERNAME")
        or file_values.get("username")
    )
    password = (
        getattr(args, "password", None)
        or os.environ.get("GPUSTACK_PASSWORD")
        or file_values.get("password")
    )
    username = username.strip() if isinstance(username, str) and username.strip() else None
    password = password if isinstance(password, str) and password else None
    if password and not username:
        raise UsageError("给了 --password 但没给 --username（账号密码登录两者都要）")
    if username and not password:
        raise UsageError("给了 --username 但没给 --password（账号密码登录两者都要）")

    timeout = getattr(args, "timeout", DEFAULT_TIMEOUT)
    if timeout is None or timeout <= 0:
        raise UsageError(f"--timeout 必须大于 0，收到: {timeout}")

    return Config(
        base_url=normalize_base_url(base_url),
        api_key=api_key,
        timeout=float(timeout),
        insecure=bool(getattr(args, "insecure", False)),
        username=username,
        password=password,
    )
