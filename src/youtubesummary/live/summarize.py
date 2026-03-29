"""直播窗口摘要逻辑。

这里把摘要状态单独持久化到 state 文件中，保证：
1. 每个窗口只会真正调用一次模型
2. 后续再次运行时，只追加新窗口，不重算旧窗口
3. `live.summary.md` 始终由状态文件重新渲染，避免解析 Markdown 反推状态
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from openai import OpenAI

from ..pipeline import TimeWindow, ensure_api_key, format_seconds, summarize_window
from .window import LiveWindow


@dataclass
class LiveSummaryEntry:
    """单个已摘要窗口的持久化记录。"""

    window_key: str
    start_seconds: int
    end_seconds: int
    chunk_ids: list[str]
    summary_text: str


@dataclass
class LiveSummaryState:
    """直播摘要状态。

    这里记录已经摘要过的窗口和累计 token 用量，后续增量运行只追加新窗口。
    """

    summary_model: str
    window_seconds: int
    summaries: list[LiveSummaryEntry] = field(default_factory=list)
    usage_totals: dict[str, int] = field(
        default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    )


def live_to_time_window(window: LiveWindow) -> TimeWindow:
    """把直播窗口转成主流程可复用的 TimeWindow。"""

    return TimeWindow(
        start=float(window.start_seconds),
        end=float(window.end_seconds),
        text=window.text,
    )


def window_key(window: LiveWindow) -> str:
    """生成窗口唯一键。

    第一版用起止时间和 chunk_ids 组合成稳定键，足够支撑当前 PoC。
    """

    return f"{window.start_seconds}-{window.end_seconds}:{','.join(window.chunk_ids)}"


def load_summary_state(
    state_path: Path,
    summary_model: str,
    window_seconds: int,
) -> LiveSummaryState:
    """读取或初始化摘要状态。

    如果摘要模型或窗口粒度变化，直接报错，避免把不同配置下的结果混在一起。
    """

    if not state_path.exists():
        return LiveSummaryState(summary_model=summary_model, window_seconds=window_seconds)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    state = LiveSummaryState(
        summary_model=payload.get("summary_model", summary_model),
        window_seconds=int(payload.get("window_seconds", window_seconds)),
        summaries=[LiveSummaryEntry(**item) for item in payload.get("summaries", [])],
        usage_totals=payload.get(
            "usage_totals",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        ),
    )
    if state.summary_model != summary_model:
        raise RuntimeError(
            f"Summary model mismatch: state={state.summary_model}, current={summary_model}"
        )
    if state.window_seconds != window_seconds:
        raise RuntimeError(
            f"Window seconds mismatch: state={state.window_seconds}, current={window_seconds}"
        )
    return state


def save_summary_state(state_path: Path, state: LiveSummaryState) -> None:
    """写回摘要状态。"""

    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary_model": state.summary_model,
        "window_seconds": state.window_seconds,
        "summaries": [asdict(item) for item in state.summaries],
        "usage_totals": state.usage_totals,
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def add_usage(total: dict[str, int], delta: dict[str, int]) -> None:
    """累计 token 用量。"""

    for key, value in delta.items():
        total[key] = total.get(key, 0) + value


def render_summary_markdown(state: LiveSummaryState) -> str:
    """把摘要状态渲染成最终 Markdown。"""

    blocks: list[str] = ["# Live Summary", ""]
    for entry in state.summaries:
        blocks.append(f"## {format_seconds(entry.start_seconds)} - {format_seconds(entry.end_seconds)}")
        blocks.append(f"- chunk_ids: {', '.join(entry.chunk_ids)}")
        blocks.append("")
        blocks.append(entry.summary_text)
        blocks.append("")

    blocks.append("## Usage")
    blocks.append(f"- input_tokens: {state.usage_totals['input_tokens']}")
    blocks.append(f"- output_tokens: {state.usage_totals['output_tokens']}")
    blocks.append(f"- total_tokens: {state.usage_totals['total_tokens']}")
    blocks.append("")
    return "\n".join(blocks).rstrip() + "\n"


def update_live_summary_state(
    windows: list[LiveWindow],
    summary_model: str,
    window_seconds: int,
    state_path: Path,
) -> tuple[LiveSummaryState, dict[str, int], int]:
    """增量更新直播摘要状态。

    只对未出现过的窗口调用模型；旧窗口直接复用已保存的摘要结果。
    返回值中的 `new_window_count` 用于观察本次是否真的有新摘要产生。
    """

    state = load_summary_state(
        state_path=state_path,
        summary_model=summary_model,
        window_seconds=window_seconds,
    )
    known_keys = {entry.window_key for entry in state.summaries}
    pending_windows = [window for window in windows if window_key(window) not in known_keys]
    usage_delta = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    if not pending_windows:
        return state, usage_delta, 0

    ensure_api_key()
    client = OpenAI()
    total = len(pending_windows)

    for index, window in enumerate(pending_windows, start=1):
        summary_text, usage = summarize_window(
            client=client,
            model=summary_model,
            window=live_to_time_window(window),
            index=index,
            total=total,
        )
        entry = LiveSummaryEntry(
            window_key=window_key(window),
            start_seconds=window.start_seconds,
            end_seconds=window.end_seconds,
            chunk_ids=window.chunk_ids,
            summary_text=summary_text,
        )
        state.summaries.append(entry)
        add_usage(state.usage_totals, usage)
        add_usage(usage_delta, usage)

    state.summaries.sort(key=lambda item: (item.start_seconds, item.end_seconds, item.window_key))
    save_summary_state(state_path, state)
    return state, usage_delta, len(pending_windows)


def write_live_summary(output_path: Path, content: str) -> None:
    """写出直播摘要报告。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
