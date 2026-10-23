"""渲染层：表格（中文双宽近似）、JSON 输出、单位换算与列表排序。

所有的人类可读输出都从这里出，保证表格列宽/单位口径在所有子命令里一致。
"""

import json
import unicodedata
from typing import Any, Dict, Iterable, List, Sequence, Tuple


WIDE_WIDTH_CHARS = ("W", "F")


def sort_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """客户端按 id 排序：服务端 sort_by 白名单每个端点不同（-id 直接 422），不能指望它。"""

    def key(item: Dict[str, Any]) -> Tuple[int, int, str]:
        value = item.get("id")
        if isinstance(value, int):
            return (0, value, "")
        text = str(value or "")
        if text.isdigit():
            return (0, int(text), "")
        return (1, 0, text)

    return sorted(items, key=key)


def json_list_payload(payload: Any, items: List[Dict[str, Any]]) -> Any:
    """--json 也输出翻页合并后的全量，保留服务端包裹形态（裸数组仍输出裸数组）。"""
    if isinstance(payload, dict):
        merged = dict(payload)
        merged["items"] = items
        return merged
    return items


def dump_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in WIDE_WIDTH_CHARS else 1 for ch in text)


def clip_text(text: str, limit: int) -> str:
    if display_width(text) <= limit:
        return text
    kept: List[str] = []
    width = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in WIDE_WIDTH_CHARS else 1
        if width + char_width > limit - 1:
            break
        kept.append(char)
        width += char_width
    return "".join(kept) + "…"


def format_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return ("%.2f" % value).rstrip("0").rstrip(".")
    return str(value)


def format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    max_column_width: int = 52,
) -> str:
    header_cells = [clip_text(str(header), max_column_width) for header in headers]
    body_cells = [
        [clip_text(format_value(cell), max_column_width) for cell in row] for row in rows
    ]
    widths = [display_width(cell) for cell in header_cells]
    for row in body_cells:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], display_width(cell))

    def render(cells: Sequence[str]) -> str:
        padded = [
            cell + " " * (widths[index] - display_width(cell))
            for index, cell in enumerate(cells)
        ]
        return "  ".join(padded).rstrip()

    lines = [render(header_cells), "  ".join("-" * width for width in widths)]
    lines.extend(render(row) for row in body_cells)
    return "\n".join(lines)


def print_table(headers: Sequence[str], rows: Sequence[Sequence[Any]], max_column_width: int = 52) -> None:
    if not rows:
        print("（无数据）")
        return
    print(format_table(headers, rows, max_column_width=max_column_width))


def pct(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return str(value)


def human_bytes(value: Any) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    for unit in units:
        if abs(number) < 1024 or unit == units[-1]:
            return f"{int(number)}B" if unit == "B" else f"{number:.1f}{unit}"
        number /= 1024
    return f"{number:.1f}PiB"


def human_duration(seconds: Any) -> str:
    if seconds is None:
        return "-"
    try:
        total = int(float(seconds))
    except (TypeError, ValueError):
        return str(seconds)
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: List[str] = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes and not days:
        parts.append(f"{minutes}分")
    return "".join(parts) or f"{total}秒"


def count_of(value: Any) -> Any:
    if isinstance(value, (list, tuple, dict)):
        return len(value)
    return value


def join_two(first: Any, second: Any) -> str:
    values = [str(value) for value in (first, second) if value]
    return " ".join(values) or "-"


def summarize_probe(value: Any) -> str:
    if isinstance(value, dict):
        if "http_status" in value:
            return f"HTTP {value['http_status']}"
        version = value.get("version")
        commit = value.get("git_commit")
        if version and commit:
            return f"{version} (git {commit[:7]})"
        return json.dumps(value, ensure_ascii=False)[:80]
    return str(value)
