"""核心处理流水线。

这里集中管理下载、转写、时间窗口聚合、摘要生成与报告写出。
"""

from __future__ import annotations

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
    """单个转写片段，保留时间戳以支持时间轴摘要。"""

    start: float
    end: float
    text: str


@dataclass
class TimeWindow:
    """将多个转写片段聚合后的时间窗口。"""

    start: float
    end: float
    text: str


def ensure_api_key() -> None:
    """确保摘要阶段所需的 OpenAI API Key 已存在。"""

    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY environment variable.")


def download_media(url: str, downloads_dir: Path) -> Path:
    """下载 YouTube 音频并返回本地媒体路径。"""

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
    """执行本地语音转写。

    这里保留每个 segment 的起止时间，后续摘要会严格按时间窗口组织，
    这样长视频的结构不会在汇总时被打平。
    """

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
    """把秒数格式化为便于阅读的时间戳。"""

    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def build_time_windows(segments: list[TranscriptSegment], window_seconds: int) -> list[TimeWindow]:
    """按照固定时间窗口聚合转写片段。

    这里按“窗口起点到当前 segment 结束时间”的跨度判断是否换窗，
    这样可以尽量保持同一时间段内容的连续性，避免切分过碎。
    """

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
    """读取 OpenAI 返回的 token 用量。"""

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
    """累计多次摘要调用的 token 用量。"""

    for key, value in delta.items():
        total[key] = total.get(key, 0) + value


def response_text(response) -> str:
    """提取 Responses API 文本输出。"""

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
    """对单个时间窗口生成结构化摘要。"""

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
    """按时间窗口生成时间轴摘要。

    这里不再做二次强压缩合并，而是按时间顺序保留各窗口摘要，
    以满足长视频复盘时对时间定位和关键观点保留的要求。
    """

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
    """清洗文件名，避免路径和特殊字符影响输出。"""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned or "file"


def build_run_stamp(start_epoch: float) -> str:
    """生成本次任务的时间戳前缀。"""

    return time.strftime("%Y%m%d-%H%M%S", time.localtime(start_epoch))


def output_directories(downloads_dir: Path) -> tuple[Path, Path, Path]:
    """确保媒体、报告、转写三个目录存在。"""

    media_dir = downloads_dir / "media"
    reports_dir = downloads_dir / "reports"
    transcripts_dir = downloads_dir / "transcripts"
    for path in (media_dir, reports_dir, transcripts_dir):
        path.mkdir(parents=True, exist_ok=True)
    return media_dir, reports_dir, transcripts_dir


def youtube_base_name(stamp: str, url: str) -> str:
    """构造 YouTube 输入的基础文件名。"""

    video_id = yt_dlp.YoutubeDL({}).extract_info(url, download=False)["id"]
    return f"{stamp}_{video_id}"


def local_base_name(stamp: str, media_path: Path) -> str:
    """构造本地媒体输入的基础文件名。"""

    return f"{stamp}_{sanitize_name(media_path.name)}"


def rename_media_file(media_path: Path, target_path: Path) -> Path:
    """将下载好的媒体文件重命名为规范化路径。"""

    target_path.parent.mkdir(parents=True, exist_ok=True)
    if media_path.resolve() == target_path.resolve():
        return media_path
    media_path.replace(target_path)
    return target_path


def default_report_path(reports_dir: Path, base_name: str) -> Path:
    """生成默认报告路径。"""

    return reports_dir / f"{base_name}.summary.md"


def transcript_path(transcripts_dir: Path, base_name: str) -> Path:
    """生成默认转写文件路径。"""

    return transcripts_dir / f"{base_name}.transcript.txt"


def media_target_path(media_dir: Path, base_name: str, suffix: str) -> Path:
    """生成下载媒体的规范化输出路径。"""

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
    """写出 Markdown 报告。"""

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
    """写出转写文本与元信息。

    这里刻意将原始转写与摘要结果分开存储，
    便于后续重复做摘要实验时直接复用转写内容。
    """

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
