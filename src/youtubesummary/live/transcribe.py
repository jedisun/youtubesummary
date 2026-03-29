"""直播 chunk 转写逻辑。

这里复用现有的 `transcribe_file()`，把单个 chunk 转成文本并追加写入直播 transcript 文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .state import ChunkRecord


@dataclass
class LiveTranscribeConfig:
    """直播转写配置。"""

    model_name: str = "small"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None


@dataclass
class LiveTranscribeResult:
    """单个 chunk 的转写结果摘要。"""

    transcript_text: str
    segment_count: int
    char_count: int
    transcript_status: str
    summary_eligible: bool


def classify_transcript(segment_count: int, char_count: int) -> str:
    """基于转写统计对 chunk 内容做分类。

    分类规则：
    - `empty`：处理成功，但没有识别到有效语音
    - `low_content`：处理成功，但识别内容过少
    - `normal`：处理成功，且内容达到可用阈值
    """

    if segment_count == 0 and char_count == 0:
        return "empty"
    if char_count < 20:
        return "low_content"
    return "normal"


def is_summary_eligible(transcript_status: str) -> bool:
    """判断该 chunk 是否应该进入后续摘要窗口。"""

    return transcript_status == "normal"


def append_live_transcript(
    transcript_path: Path,
    chunk_id: str,
    result: LiveTranscribeResult,
) -> None:
    """把单个 chunk 的转写结果追加到 transcript 文件。

    第一版先按 chunk 追加纯文本，后续再扩展成带全局时间轴的 segment 持久化。
    """

    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{chunk_id}]\n")
        handle.write(f"transcript_status: {result.transcript_status}\n")
        handle.write(f"summary_eligible: {str(result.summary_eligible).lower()}\n")
        handle.write(f"segment_count: {result.segment_count}\n")
        handle.write(f"char_count: {result.char_count}\n")
        handle.write(f"{result.transcript_text.strip()}\n\n")


def handle_chunk_transcription(
    record: ChunkRecord,
    transcript_path: Path,
    config: LiveTranscribeConfig,
) -> LiveTranscribeResult:
    """处理单个 chunk 的真实转写。"""

    from ..pipeline import transcribe_file

    segments, transcript_text = transcribe_file(
        media_path=Path(record.chunk_path),
        model_name=config.model_name,
        device=config.device,
        compute_type=config.compute_type,
        language=config.language,
    )
    segment_count = len(segments)
    char_count = len(transcript_text.replace("\n", ""))
    transcript_status = classify_transcript(segment_count=segment_count, char_count=char_count)
    result = LiveTranscribeResult(
        transcript_text=transcript_text,
        segment_count=segment_count,
        char_count=char_count,
        transcript_status=transcript_status,
        summary_eligible=is_summary_eligible(transcript_status),
    )
    append_live_transcript(
        transcript_path=transcript_path,
        chunk_id=record.chunk_id,
        result=result,
    )
    return result
