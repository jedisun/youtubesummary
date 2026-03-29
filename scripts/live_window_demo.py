#!/usr/bin/env python3
"""直播窗口构建验证脚本。

从状态文件和 transcript 文件中构造可摘要窗口，只消费
`summary_eligible=true` 的 chunk。
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
from youtubesummary.live.window import build_live_windows, parse_live_transcript_entries


def format_seconds(seconds: int) -> str:
    """把秒数格式化成 mm:ss 或 hh:mm:ss。"""

    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build live summary windows from state + transcript.")
    parser.add_argument("--run-dir", default=None, help="Run directory produced by live_capture_demo.py")
    parser.add_argument("--state-file", default="downloads/live/state/chunks_state.json", help="Chunk state JSON file")
    parser.add_argument("--transcript-file", default="downloads/live/transcripts/live.transcript.txt", help="Live transcript file")
    parser.add_argument("--chunk-seconds", type=int, default=30, help="Duration of one chunk in seconds")
    parser.add_argument("--window-seconds", type=int, default=300, help="Duration of one summary window in seconds")
    parser.add_argument(
        "--output-file",
        default="downloads/live/reports/live.windows.md",
        help="Window preview output file",
    )
    return parser.parse_args()


def main() -> int:
    """构建并写出窗口预览。"""

    args = parse_args()
    if args.run_dir:
        layout = live_run_layout(Path(args.run_dir))
        state_file = layout.state_file
        transcript_file = layout.transcript_file
        output_path = layout.window_report_file
    else:
        state_file = Path(args.state_file)
        transcript_file = Path(args.transcript_file)
        output_path = Path(args.output_file)

    store = ChunkStateStore(state_file)
    records = store.load_records()
    entries = parse_live_transcript_entries(transcript_file)
    windows = build_live_windows(
        records=records,
        transcript_entries=entries,
        chunk_seconds=args.chunk_seconds,
        window_seconds=args.window_seconds,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Live Windows Preview", ""]
    for index, window in enumerate(windows, start=1):
        lines.append(f"## Window {index}: {format_seconds(window.start_seconds)} - {format_seconds(window.end_seconds)}")
        lines.append(f"- chunk_ids: {', '.join(window.chunk_ids)}")
        lines.append("")
        lines.append(window.text)
        lines.append("")

    output_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"[window] state_file={state_file}")
    print(f"[window] transcript_file={transcript_file}")
    print(f"[window] output_file={output_path}")
    print(f"[window] window_count={len(windows)}")
    for index, window in enumerate(windows, start=1):
        print(
            f"[window] #{index} {format_seconds(window.start_seconds)}-{format_seconds(window.end_seconds)} "
            f"chunks={len(window.chunk_ids)} chars={len(window.text.replace(chr(10), ''))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
