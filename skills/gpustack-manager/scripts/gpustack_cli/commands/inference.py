"""推理面：chat（含 --stream）与 embeddings 冒烟测试。

走 /v1 OpenAI 兼容接口；--json 时优先出原始 JSON（此时关闭流式）。
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .. import EXIT_OK, ApiError, add_sub
from ..config import Config
from ..http import BODY_SNIPPET, _opener, _request, require_ok
from ..output import dump_json, format_table


def chat_payload(args: argparse.Namespace) -> Dict[str, Any]:
    messages: List[Dict[str, str]] = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})
    payload: Dict[str, Any] = {
        "model": args.model,
        "messages": messages,
        "stream": bool(args.stream and not args.json),
    }
    if args.temperature is not None:
        payload["temperature"] = args.temperature
    if args.max_tokens is not None:
        payload["max_tokens"] = args.max_tokens
    return payload


def cmd_chat(cfg: Config, args: argparse.Namespace) -> int:
    payload = chat_payload(args)
    if payload["stream"]:
        return stream_chat(cfg, payload)
    result = require_ok(_request(cfg, "POST", "/v1/chat/completions", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    choices = result.get("choices") or []
    message = (choices[0] or {}).get("message") or {}
    print(message.get("content") or "(空回复)")
    usage = result.get("usage") or {}
    if usage:
        print(
            f"\ntokens: prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')} total={usage.get('total_tokens')}"
        )
    return EXIT_OK


def stream_chat(cfg: Config, payload: Dict[str, Any]) -> int:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(cfg.base_url + "/v1/chat/completions", data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "text/event-stream")
    if cfg.api_key:
        request.add_header("Authorization", "Bearer " + cfg.api_key)
    try:
        raw = _opener(cfg.insecure, True).open(request, timeout=cfg.timeout)
    except urllib.error.HTTPError as exc:
        snippet = exc.read().decode("utf-8", errors="replace")[:BODY_SNIPPET]
        raise ApiError(f"POST /v1/chat/completions -> HTTP {exc.code}: {snippet}") from exc
    except OSError as exc:
        raise ApiError(f"POST /v1/chat/completions -> 网络错误: {exc}") from exc

    with raw:
        try:
            for line in raw:
                text = line.decode("utf-8", errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("delta") or {}).get("content")
                if content:
                    sys.stdout.write(content)
                    sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        except BrokenPipeError:
            raise
        except OSError as exc:
            raise ApiError(f"POST /v1/chat/completions -> 流式读取中断: {exc}") from exc
    print()
    return EXIT_OK


def cmd_embeddings(cfg: Config, args: argparse.Namespace) -> int:
    payload = {"model": args.model, "input": args.input}
    result = require_ok(_request(cfg, "POST", "/v1/embeddings", data=payload)).json() or {}
    if args.json:
        dump_json(result)
        return EXIT_OK
    data = result.get("data") or []
    if not data:
        print(result)
        return EXIT_OK
    # 自定义后端的响应结构未必是 OpenAI 的 embedding 字段：bge-custom 实测返回 data[].dense
    first = data[0] if isinstance(data[0], dict) else {}
    vector = first.get("embedding") or first.get("dense") or first.get("vector") or []
    if not vector:
        # 结构未知时把键名打出来，别静默显示 0 维
        print(f"（未识别的向量字段，data[0] 的键：{sorted(first)}）")
    preview = ", ".join(f"{value:.4f}" for value in vector[:8])
    usage = result.get("usage") or {}
    print(
        format_table(
            ["字段", "值"],
            [
                ["模型", result.get("model") or args.model],
                ["向量维度", len(vector)],
                ["前 8 维", preview],
                ["usage", json.dumps(usage, ensure_ascii=False)],
            ],
        )
    )
    return EXIT_OK


def register(subparsers: Any, common: argparse.ArgumentParser) -> None:
    """注册 chat/embeddings。"""
    # 推理面
    p = add_sub(subparsers, "chat", "OpenAI 兼容 chat 冒烟测试（/v1/chat/completions）", common)
    p.add_argument("--model", required=True, help="模型名")
    p.add_argument("--prompt", required=True, help="用户消息")
    p.add_argument("--system", help="system 消息")
    p.add_argument("--temperature", type=float, help="采样温度")
    p.add_argument("--max-tokens", dest="max_tokens", type=int, help="最大生成 token 数")
    p.add_argument("--stream", action="store_true", help="流式输出（与 --json 同时用时以 --json 优先）")
    p.set_defaults(func=cmd_chat)

    p = add_sub(subparsers, "embeddings", "OpenAI 兼容 embeddings（/v1/embeddings）", common)
    p.add_argument("--model", required=True, help="模型名")
    p.add_argument("--input", required=True, help="输入文本")
    p.set_defaults(func=cmd_embeddings)
