"""直播 transcript 窗口构建。

这里先实现一个最小版本：
1. 从 transcript 文件解析每个 chunk 的文本
2. 结合状态表中的 `summary_eligible` 字段过滤 chunk
3. 按固定 chunk 时长和固定窗口时长构造摘要输入窗口
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .state import CHUNK_STATUS_PROCESSED, ChunkRecord


@dataclass
class LiveTranscriptEntry:
    """单个 chunk 的 transcript 条目。"""

    chunk_id: str
    transcript_status: str | None
    summary_eligible: bool
    segment_count: int
    char_count: int
    text: str


@dataclass
class LiveWindow:
    """直播窗口摘要输入。"""

    start_seconds: int
    end_seconds: int
    chunk_ids: list[str]
    text: str


def parse_bool(value: str) -> bool:
    """解析 transcript 文件中的布尔值。"""

    return value.strip().lower() == "true"


def parse_int(value: str) -> int:
    """解析 transcript 文件中的整数字段。"""

    try:
        return int(value.strip())
    except ValueError:
        return 0


def parse_live_transcript_entries(transcript_path: Path) -> dict[str, LiveTranscriptEntry]:
    """解析直播 transcript 文件，恢复每个 chunk 的正文和元信息。"""

    if not transcript_path.exists():
        return {}

    entries: dict[str, LiveTranscriptEntry] = {}
    current_chunk_id: str | None = None
    current_status: str | None = None
    current_summary_eligible = False
    current_segment_count = 0
    current_char_count = 0
    current_text_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current_chunk_id, current_status, current_summary_eligible
        nonlocal current_segment_count, current_char_count, current_text_lines
        if current_chunk_id is None:
            return
        text = "\n".join(current_text_lines).strip()
        entries[current_chunk_id] = LiveTranscriptEntry(
            chunk_id=current_chunk_id,
            transcript_status=current_status,
            summary_eligible=current_summary_eligible,
            segment_count=current_segment_count,
            char_count=current_char_count,
            text=text,
        )
        current_chunk_id = None
        current_status = None
        current_summary_eligible = False
        current_segment_count = 0
        current_char_count = 0
        current_text_lines = []

    for raw_line in transcript_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("[") and line.endswith("]"):
            flush_current()
            current_chunk_id = line[1:-1]
            continue
        if current_chunk_id is None:
            continue
        if line.startswith("transcript_status: "):
            current_status = line.split(": ", 1)[1].strip() or None
            continue
        if line.startswith("summary_eligible: "):
            current_summary_eligible = parse_bool(line.split(": ", 1)[1])
            continue
        if line.startswith("segment_count: "):
            current_segment_count = parse_int(line.split(": ", 1)[1])
            continue
        if line.startswith("char_count: "):
            current_char_count = parse_int(line.split(": ", 1)[1])
            continue
        current_text_lines.append(line)

    flush_current()
    return entries


def processed_chunks(records: list[ChunkRecord]) -> list[ChunkRecord]:
    """返回已完成转写的 chunk，按 chunk_id 排序。"""

    return sorted(
        [record for record in records if record.status == CHUNK_STATUS_PROCESSED],
        key=lambda item: item.chunk_id,
    )


def build_live_windows(
    records: list[ChunkRecord],
    transcript_entries: dict[str, LiveTranscriptEntry],
    chunk_seconds: int,
    window_seconds: int,
) -> list[LiveWindow]:
    """基于 chunk 顺序构造直播摘要窗口。

    时间轴基于“所有 processed chunk 的顺序”推进，
    但只有 `summary_eligible=true` 的 chunk 文本才会进入窗口正文。
    """

    if chunk_seconds <= 0:
        raise ValueError("chunk_seconds must be > 0")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")

    records_in_order = processed_chunks(records)
    if not records_in_order:
        return []

    chunks_per_window = max(1, window_seconds // chunk_seconds)
    windows: list[LiveWindow] = []

    for base_index in range(0, len(records_in_order), chunks_per_window):
        window_records = records_in_order[base_index : base_index + chunks_per_window]
        start_seconds = base_index * chunk_seconds
        end_seconds = start_seconds + len(window_records) * chunk_seconds
        included_chunk_ids: list[str] = []
        included_texts: list[str] = []

        for record in window_records:
            if not record.summary_eligible:
                continue
            entry = transcript_entries.get(record.chunk_id)
            if entry is None or not entry.text.strip():
                continue
            included_chunk_ids.append(record.chunk_id)
            included_texts.append(entry.text.strip())

        if not included_texts:
            continue

        windows.append(
            LiveWindow(
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                chunk_ids=included_chunk_ids,
                text="\n".join(included_texts),
            )
        )

    return windows
