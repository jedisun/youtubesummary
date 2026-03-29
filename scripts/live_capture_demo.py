#!/usr/bin/env python3
"""YouTube 直播抓流验证脚本。

这个脚本只验证直播输入层是否可行：
1. 使用 yt-dlp 解析直播流
2. 使用 ffmpeg 做单次限时抓取并按固定时长切片
3. 在命令自然结束后统计验证结果

该脚本不做转写和摘要，只服务于直播 PoC 的前置验证。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from youtubesummary.live.paths import build_run_id, build_run_stamp, ensure_live_run_layout


@dataclass
class CaptureResult:
    """记录本次抓流验证的结果摘要。"""

    source_url: str
    run_dir: str
    resolved_stream_url: str
    chunk_dir: str
    log_file: str
    chunk_count: int
    chunk_seconds: int
    run_seconds: int
    wall_clock_seconds: float
    ffmpeg_exit_code: int | None


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Validate YouTube live capture with yt-dlp and ffmpeg.")
    parser.add_argument("url", help="YouTube live URL")
    parser.add_argument("--output-dir", default="downloads/live", help="Base output directory for live capture files")
    parser.add_argument("--run-dir", default=None, help="Explicit run directory; defaults to downloads/live/{timestamp}_{video_id}")
    parser.add_argument("--chunk-seconds", type=int, default=30, help="Length of each audio chunk in seconds")
    parser.add_argument("--run-seconds", type=int, default=600, help="How long to run the capture demo")
    parser.add_argument("--audio-format", default="wav", choices=["wav"], help="Output audio format")
    return parser.parse_args()


def resolve_stream_url(url: str) -> str:
    """解析直播流地址。

    这里复用 yt-dlp 的解析能力，而不是自己直接处理页面逻辑。
    对直播场景而言，解析层经常是最脆弱的一环，所以单独抽出来便于排错。
    """

    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    stream_url = info.get("url")
    if stream_url:
        return stream_url

    formats = info.get("formats") or []
    audio_only_formats = [
        fmt
        for fmt in formats
        if fmt.get("url") and fmt.get("vcodec") == "none" and fmt.get("acodec") not in (None, "none")
    ]
    if audio_only_formats:
        # 这里优先选择音频码率更高的音频流。
        best_audio = max(audio_only_formats, key=lambda fmt: float(fmt.get("abr") or fmt.get("tbr") or 0))
        return str(best_audio["url"])

    playable_formats = [
        fmt for fmt in formats if fmt.get("url") and fmt.get("acodec") not in (None, "none")
    ]
    if not playable_formats:
        raise RuntimeError("Failed to resolve a playable live stream URL.")

    # 某些直播源没有独立音频流，只提供带音视频的 HLS 变体。
    # 这里退回到最低总码率的可播放流，后续由 ffmpeg 丢弃视频轨，仅保留音频。
    lowest_bandwidth_variant = min(playable_formats, key=lambda fmt: float(fmt.get("tbr") or 0))
    return str(lowest_bandwidth_variant["url"])


def chunk_output_pattern(chunk_dir: Path, audio_format: str) -> str:
    """生成 ffmpeg 切片输出模板。"""

    return str(chunk_dir / f"chunk_%05d.{audio_format}")


def build_ffmpeg_command(
    stream_url: str,
    output_pattern: str,
    chunk_seconds: int,
    run_seconds: int,
) -> list[str]:
    """构造 ffmpeg 命令。

    关键点：
    - 使用单次限时抓取，避免把长时间常驻进程控制问题混入本轮验证
    - 统一转成 16kHz 单声道 PCM，便于后续 ASR 直接复用
    - 使用 segment muxer 固定切片，专注验证 30 秒 chunk 是否可产出
    """

    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-t",
        str(run_seconds),
        "-i",
        stream_url,
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        "-f",
        "segment",
        "-segment_time",
        str(chunk_seconds),
        "-reset_timestamps",
        "1",
        output_pattern,
    ]


def list_chunks(chunk_dir: Path, audio_format: str) -> list[Path]:
    """列出已生成 chunk，按文件名排序。"""

    return sorted(chunk_dir.glob(f"*.{audio_format}"))


def write_result_file(result_path: Path, result: CaptureResult) -> Path:
    """把验证结果写入 JSON，便于后续人工查看或自动化分析。"""

    result_path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result_path


def main() -> int:
    """执行直播抓流验证。"""

    args = parse_args()
    # 本次运行的时间基准只在入口创建一次，后续全部复用这个 stamp。
    stamp = build_run_stamp()
    run_dir = Path(args.run_dir) if args.run_dir else Path(args.output_dir) / build_run_id(args.url, stamp)
    layout = ensure_live_run_layout(run_dir)
    log_file = layout.ffmpeg_log_file

    print("[1/4] resolving live stream URL...", flush=True)
    stream_url = resolve_stream_url(args.url)
    print("[2/4] starting bounded ffmpeg segment capture...", flush=True)

    command = build_ffmpeg_command(
        stream_url=stream_url,
        output_pattern=chunk_output_pattern(layout.chunk_dir, args.audio_format),
        chunk_seconds=args.chunk_seconds,
        run_seconds=args.run_seconds,
    )

    import time

    start_time = time.perf_counter()
    with log_file.open("wb") as log_handle:
        completed = subprocess.run(
            command,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
    wall_clock_seconds = time.perf_counter() - start_time
    exit_code = completed.returncode

    chunks = list_chunks(layout.chunk_dir, args.audio_format)
    result = CaptureResult(
        source_url=args.url,
        run_dir=str(layout.run_dir),
        resolved_stream_url=stream_url,
        chunk_dir=str(layout.chunk_dir),
        log_file=str(log_file),
        chunk_count=len(chunks),
        chunk_seconds=args.chunk_seconds,
        run_seconds=args.run_seconds,
        wall_clock_seconds=wall_clock_seconds,
        ffmpeg_exit_code=exit_code,
    )
    result_file = write_result_file(layout.capture_result_file, result)

    print("[3/4] capture finished", flush=True)
    print(
        f"[result] chunk_count={result.chunk_count} chunk_seconds={result.chunk_seconds} "
        f"wall_clock_seconds={result.wall_clock_seconds:.2f} ffmpeg_exit_code={result.ffmpeg_exit_code}"
    )
    print(f"[4/4] run_dir: {layout.run_dir}", flush=True)
    print(f"[logs] {log_file}", flush=True)
    print(f"[result-file] written: {result_file}", flush=True)

    if result.chunk_count == 0:
        print("No chunks were produced during the capture demo.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
