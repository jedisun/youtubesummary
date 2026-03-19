#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yt_dlp
from faster_whisper import WhisperModel
from openai import OpenAI


WINDOW_PROMPT = """你是一个中文视频内容整理助手。
请基于用户提供的某一时间段转写内容，输出以下格式。不要因为追求简洁而省略重要观点、关键数字、价位、条件、判断或结论：

### 主题
用 1 到 2 句话概括本时间段主题。

### 内容摘要
用一段较完整的中文说明本时间段具体讲了什么。

### 重要观点
- 若本时间段包含明确观点、建议、判断、结论、关键数字或关键条件，则逐条列出
- 若无明显观点，写 `- 无`
"""


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


@dataclass
class TimeWindow:
    start: float
    end: float
    text: str


def parse_args() -> argparse.Namespace:
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


def ensure_api_key() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY environment variable.")


def download_media(url: str, downloads_dir: Path) -> Path:
    downloads_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(downloads_dir / "%(id)s.%(ext)s")
    options = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        media_path = Path(ydl.prepare_filename(info))
    return media_path


def transcribe_file(
    media_path: Path,
    model_name: str,
    device: str,
    compute_type: str,
    language: str | None = None,
) -> tuple[list[TranscriptSegment], str]:
    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(str(media_path), language=language, vad_filter=True)
    items: list[TranscriptSegment] = []
    parts: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            items.append(TranscriptSegment(start=float(segment.start), end=float(segment.end), text=text))
            parts.append(text)
    return items, "\n".join(parts)


def format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_time_windows(segments: list[TranscriptSegment], window_seconds: int) -> list[TimeWindow]:
    if not segments:
        return []
    windows: list[TimeWindow] = []
    current_start = segments[0].start
    current_end = segments[0].end
    current_texts = [segments[0].text]

    for segment in segments[1:]:
        if segment.end - current_start <= window_seconds:
            current_end = segment.end
            current_texts.append(segment.text)
            continue
        windows.append(TimeWindow(start=current_start, end=current_end, text="\n".join(current_texts)))
        current_start = segment.start
        current_end = segment.end
        current_texts = [segment.text]

    windows.append(TimeWindow(start=current_start, end=current_end, text="\n".join(current_texts)))
    return windows


def read_usage(response) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def add_usage(total: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] = total.get(key, 0) + value


def response_text(response) -> str:
    text = getattr(response, "output_text", "")
    if text:
        return text.strip()
    return str(response).strip()


def summarize_window(
    client: OpenAI,
    model: str,
    window: TimeWindow,
    index: int,
    total: int,
) -> tuple[str, dict[str, int]]:
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": WINDOW_PROMPT},
            {
                "role": "user",
                "content": (
                    f"下面是第 {index}/{total} 个时间段的转写内容。\n"
                    f"时间范围：{format_seconds(window.start)} - {format_seconds(window.end)}\n\n"
                    f"{window.text}"
                ),
            },
        ],
    )
    return response_text(response), read_usage(response)


def summarize_text(
    segments: list[TranscriptSegment],
    summary_model: str,
    time_window_seconds: int,
    max_windows: int,
) -> tuple[str, dict[str, int]]:
    client = OpenAI()
    windows = build_time_windows(segments, time_window_seconds)
    if max_windows > 0:
        windows = windows[:max_windows]
    if not windows:
        raise RuntimeError("Transcript is empty, cannot summarize.")

    usage_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    window_summaries: list[str] = []
    window_total = len(windows)

    for idx, window in enumerate(windows, start=1):
        window_summary, usage = summarize_window(client, summary_model, window, idx, window_total)
        window_summaries.append(f"## {format_seconds(window.start)} - {format_seconds(window.end)}\n{window_summary}")
        add_usage(usage_totals, usage)
        print(
            f"[usage/window {idx}/{window_total}] input_tokens={usage['input_tokens']} "
            f"output_tokens={usage['output_tokens']} total_tokens={usage['total_tokens']}",
            file=sys.stderr,
        )
    return "# 视频内容摘要\n\n" + "\n\n".join(window_summaries), usage_totals


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned or "file"


def build_run_stamp(start_epoch: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(start_epoch))


def output_directories(downloads_dir: Path) -> tuple[Path, Path, Path]:
    media_dir = downloads_dir / "media"
    reports_dir = downloads_dir / "reports"
    transcripts_dir = downloads_dir / "transcripts"
    for path in (media_dir, reports_dir, transcripts_dir):
        path.mkdir(parents=True, exist_ok=True)
    return media_dir, reports_dir, transcripts_dir


def youtube_base_name(stamp: str, url: str) -> str:
    video_id = yt_dlp.YoutubeDL({}).extract_info(url, download=False)["id"]
    return f"{stamp}_{video_id}"


def local_base_name(stamp: str, media_path: Path) -> str:
    return f"{stamp}_{sanitize_name(media_path.name)}"


def rename_media_file(media_path: Path, target_path: Path) -> Path:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if media_path.resolve() == target_path.resolve():
        return media_path
    media_path.replace(target_path)
    return target_path


def default_report_path(reports_dir: Path, base_name: str) -> Path:
    return reports_dir / f"{base_name}.summary.md"


def transcript_path(transcripts_dir: Path, base_name: str) -> Path:
    return transcripts_dir / f"{base_name}.transcript.txt"


def media_target_path(media_dir: Path, base_name: str, suffix: str) -> Path:
    return media_dir / f"{base_name}.media{suffix}"


def write_report(
    output_path: Path,
    source: str,
    media_path: Path,
    transcript: str,
    summary: str,
    usage: dict[str, int],
    elapsed_seconds: float,
    download_seconds: float,
    transcribe_seconds: float,
    summarize_seconds: float,
    summary_model: str,
    whisper_model: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = (
        "# Processing Report\n\n"
        "## Metrics\n"
        f"- source: {source}\n"
        f"- media_file: {media_path}\n"
        f"- transcript_chars: {len(transcript)}\n"
        f"- elapsed_seconds: {elapsed_seconds:.2f}\n"
        f"- download_seconds: {download_seconds:.2f}\n"
        f"- transcribe_seconds: {transcribe_seconds:.2f}\n"
        f"- summarize_seconds: {summarize_seconds:.2f}\n"
        f"- whisper_model: {whisper_model}\n"
        f"- summary_model: {summary_model}\n"
        f"- input_tokens: {usage['input_tokens']}\n"
        f"- output_tokens: {usage['output_tokens']}\n"
        f"- total_tokens: {usage['total_tokens']}\n\n"
        "## Summary\n\n"
        f"{summary.strip()}\n"
    )
    output_path.write_text(report, encoding="utf-8")


def write_transcript(
    output_path: Path,
    source: str,
    media_path: Path,
    transcript: str,
    whisper_model: str,
    language: str | None,
    device: str,
    compute_type: str,
    created_at: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"source: {source}\n"
        f"media_file: {media_path}\n"
        f"created_at: {created_at}\n"
        f"whisper_model: {whisper_model}\n"
        f"language: {language or 'auto'}\n"
        f"device: {device}\n"
        f"compute_type: {compute_type}\n"
        f"transcript_chars: {len(transcript)}\n\n"
        "[TRANSCRIPT]\n"
        f"{transcript.strip()}\n"
    )
    output_path.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
