#!/usr/bin/env python3
"""直播摘要验证脚本。

最小流程：
1. 从状态文件和 transcript 文件构造窗口
2. 只消费 `summary_eligible=true` 的 chunk
3. 调用 OpenAI 生成时间轴摘要
4. 写出 summary Markdown
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from youtubesummary.live.state import ChunkStateStore
from youtubesummary.live.paths import live_run_layout
from youtubesummary.live.summarize import (
    render_summary_markdown,
    update_live_summary_state,
    write_live_summary,
)
from youtubesummary.live.window import build_live_windows, parse_live_transcript_entries


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Summarize live windows with OpenAI.")
    parser.add_argument("--run-dir", default=None, help="Run directory produced by live_capture_demo.py")
    parser.add_argument("--state-file", default="downloads/live/state/chunks_state.json", help="Chunk state JSON file")
    parser.add_argument("--transcript-file", default="downloads/live/transcripts/live.transcript.txt", help="Live transcript file")
    parser.add_argument("--chunk-seconds", type=int, default=30, help="Duration of one chunk in seconds")
    parser.add_argument("--window-seconds", type=int, default=300, help="Duration of one summary window in seconds")
    parser.add_argument("--summary-model", default="gpt-5-mini", help="OpenAI summary model")
    parser.add_argument(
        "--output-file",
        default="downloads/live/reports/live.summary.md",
        help="Summary output file",
    )
    return parser.parse_args()


def main() -> int:
    """构建窗口并生成直播摘要。"""

    args = parse_args()
    if args.run_dir:
        layout = live_run_layout(Path(args.run_dir))
        state_file = layout.state_file
        transcript_file = layout.transcript_file
        output_path = layout.summary_file
        summary_state_file = layout.summary_state_file
    else:
        state_file = Path(args.state_file)
        transcript_file = Path(args.transcript_file)
        output_path = Path(args.output_file)
        summary_state_file = output_path.parent / "summary_state.json"

    store = ChunkStateStore(state_file)
    records = store.load_records()
    entries = parse_live_transcript_entries(transcript_file)
    windows = build_live_windows(
        records=records,
        transcript_entries=entries,
        chunk_seconds=args.chunk_seconds,
        window_seconds=args.window_seconds,
    )
    summary_state, usage_delta, new_window_count = update_live_summary_state(
        windows=windows,
        summary_model=args.summary_model,
        window_seconds=args.window_seconds,
        state_path=summary_state_file,
    )
    summary = render_summary_markdown(summary_state)
    write_live_summary(output_path, summary)

    print(f"[summary] state_file={state_file}")
    print(f"[summary] transcript_file={transcript_file}")
    print(f"[summary] summary_state_file={summary_state_file}")
    print(f"[summary] output_file={output_path}")
    print(f"[summary] window_count={len(windows)}")
    print(f"[summary] new_window_count={new_window_count}")
    print(
        f"[summary] usage_delta input_tokens={usage_delta['input_tokens']} "
        f"output_tokens={usage_delta['output_tokens']} total_tokens={usage_delta['total_tokens']}"
    )
    print(
        f"[summary] usage_total input_tokens={summary_state.usage_totals['input_tokens']} "
        f"output_tokens={summary_state.usage_totals['output_tokens']} "
        f"total_tokens={summary_state.usage_totals['total_tokens']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
