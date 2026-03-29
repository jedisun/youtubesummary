#!/usr/bin/env python3
"""直播 chunk 转写验证脚本。

这个脚本读取 `downloads/live/chunks/` 中已经完成的 chunk，
通过 watcher 驱动状态流转，并使用 faster-whisper 做真实转写。
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
from youtubesummary.live.transcribe import LiveTranscribeConfig, handle_chunk_transcription
from youtubesummary.live.watcher import watch_chunks


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Transcribe live chunks with watcher + faster-whisper.")
    parser.add_argument("--run-dir", default=None, help="Run directory produced by live_capture_demo.py")
    parser.add_argument("--chunk-dir", default="downloads/live/chunks", help="Directory containing captured live chunks")
    parser.add_argument("--state-file", default="downloads/live/state/chunks_state.json", help="Persistent chunk state file")
    parser.add_argument(
        "--transcript-file",
        default="downloads/live/transcripts/live.transcript.txt",
        help="Transcript output file",
    )
    parser.add_argument("--model-name", default="small", help="faster-whisper model name")
    parser.add_argument("--device", default="cpu", help="Whisper device, e.g. cpu or cuda")
    parser.add_argument("--compute-type", default="int8", help="Whisper compute type")
    parser.add_argument("--language", default=None, help="Optional language hint for transcription")
    parser.add_argument("--poll-seconds", type=int, default=2, help="Watcher polling interval")
    parser.add_argument("--processing-timeout", type=int, default=30, help="Timeout before processing is treated as interrupted")
    parser.add_argument("--heartbeat-interval", type=int, default=10, help="Heartbeat refresh interval for updated_at")
    parser.add_argument("--once", action="store_true", help="Process at most one available chunk and exit")
    return parser.parse_args()


def main() -> int:
    """执行 watcher + 真实转写的最小闭环。"""

    args = parse_args()
    if args.run_dir:
        layout = live_run_layout(Path(args.run_dir))
        chunk_dir = layout.chunk_dir
        state_file = layout.state_file
        transcript_file = layout.transcript_file
    else:
        chunk_dir = Path(args.chunk_dir)
        state_file = Path(args.state_file)
        transcript_file = Path(args.transcript_file)

    if not chunk_dir.exists():
        raise RuntimeError(f"Chunk directory not found: {chunk_dir}")

    store = ChunkStateStore(state_file)
    config = LiveTranscribeConfig(
        model_name=args.model_name,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
    )

    print(f"[watcher] chunk_dir={chunk_dir}")
    print(f"[watcher] state_file={state_file}")
    print(f"[watcher] transcript_file={transcript_file}")

    def handler(record):
        print(f"[transcribe] processing {record.chunk_id}")
        result = handle_chunk_transcription(
            record=record,
            transcript_path=transcript_file,
            config=config,
        )
        print(
            f"[transcribe] done {record.chunk_id} "
            f"status={result.transcript_status} "
            f"summary_eligible={str(result.summary_eligible).lower()} "
            f"segments={result.segment_count} "
            f"chars={result.char_count}"
        )
        return result

    watch_chunks(
        chunk_dir=chunk_dir,
        store=store,
        handler=handler,
        poll_seconds=args.poll_seconds,
        processing_timeout=args.processing_timeout,
        heartbeat_interval=args.heartbeat_interval,
        once=args.once,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
