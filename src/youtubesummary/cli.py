"""命令行入口。

该文件负责参数解析，并调用处理流水线完成下载、转写、摘要和落盘。
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Download a YouTube video, transcribe it locally, and summarize it in Chinese."
    )
    parser.add_argument("url", nargs="?", help="YouTube video URL")
    parser.add_argument("--media-file", default=None, help="Analyze a local media file instead of downloading from YouTube")
    parser.add_argument("--downloads-dir", default="downloads", help="Directory for downloaded media")
    parser.add_argument("--model-name", default="small", help="faster-whisper model name")
    parser.add_argument("--device", default="cpu", help="Whisper device, e.g. cpu or cuda")
    parser.add_argument("--compute-type", default="int8", help="Whisper compute type")
    parser.add_argument("--language", default=None, help="Optional language hint for transcription")
    parser.add_argument("--summary-model", default="gpt-5-mini", help="OpenAI model for summaries")
    parser.add_argument("--time-window-seconds", type=int, default=180, help="Time window size for timeline summaries")
    parser.add_argument("--max-windows", type=int, default=0, help="Limit processed windows, 0 means no limit")
    parser.add_argument("--output-file", default=None, help="Optional path for summary report output")
    args = parser.parse_args()
    if not args.url and not args.media_file:
        parser.error("either a YouTube URL or --media-file must be provided")
    if args.url and args.media_file:
        parser.error("provide either a YouTube URL or --media-file, not both")
    return args


def main() -> int:
    """执行完整处理流程。"""

    args = parse_args()

    from .pipeline import (
        build_run_stamp,
        default_report_path,
        download_media,
        ensure_api_key,
        local_base_name,
        media_target_path,
        output_directories,
        rename_media_file,
        summarize_text,
        transcript_path,
        transcribe_file,
        write_report,
        write_transcript,
        youtube_base_name,
    )
    ensure_api_key()
    start_perf = time.perf_counter()
    start_epoch = time.time()
    stamp = build_run_stamp(start_epoch)
    downloads_dir = Path(args.downloads_dir)
    media_dir, reports_dir, transcripts_dir = output_directories(downloads_dir)

    if args.media_file:
        download_seconds = 0.0
        media_path = Path(args.media_file).expanduser().resolve()
        if not media_path.is_file():
            raise RuntimeError(f"Local media file not found: {media_path}")
        source = f"local-file:{media_path}"
        base_name = local_base_name(stamp, media_path)
        print(f"[1/4] using local media: {media_path}")
    else:
        download_start = time.perf_counter()
        base_name = youtube_base_name(stamp, args.url)
        media_path = download_media(args.url, media_dir)
        media_path = rename_media_file(media_path, media_target_path(media_dir, base_name, media_path.suffix))
        download_seconds = time.perf_counter() - download_start
        source = args.url
        print(f"[1/4] downloaded: {media_path}")

    transcribe_start = time.perf_counter()
    segments, transcript = transcribe_file(
        media_path=media_path,
        model_name=args.model_name,
        device=args.device,
        compute_type=args.compute_type,
        language=args.language,
    )
    transcribe_seconds = time.perf_counter() - transcribe_start
    print(f"[2/4] transcript chars: {len(transcript)}")

    summarize_start = time.perf_counter()
    summary, usage = summarize_text(
        segments=segments,
        summary_model=args.summary_model,
        time_window_seconds=args.time_window_seconds,
        max_windows=args.max_windows,
    )
    summarize_seconds = time.perf_counter() - summarize_start
    elapsed_seconds = time.perf_counter() - start_perf
    output_path = Path(args.output_file) if args.output_file else default_report_path(reports_dir, base_name)
    transcript_output_path = transcript_path(transcripts_dir, base_name)
    write_transcript(
        output_path=transcript_output_path,
        source=source,
        media_path=media_path,
        transcript=transcript,
        whisper_model=args.model_name,
        language=args.language,
        device=args.device,
        compute_type=args.compute_type,
        created_at=stamp,
    )
    write_report(
        output_path=output_path,
        source=source,
        media_path=media_path,
        transcript=transcript,
        summary=summary,
        usage=usage,
        elapsed_seconds=elapsed_seconds,
        download_seconds=download_seconds,
        transcribe_seconds=transcribe_seconds,
        summarize_seconds=summarize_seconds,
        summary_model=args.summary_model,
        whisper_model=args.model_name,
    )
    print("[3/4] summary:")
    print(summary)
    print(
        f"[4/4] usage: input_tokens={usage['input_tokens']}, "
        f"output_tokens={usage['output_tokens']}, total_tokens={usage['total_tokens']}"
    )
    print(f"[report] written: {output_path}")
    print(f"[transcript] written: {transcript_output_path}")
    return 0
