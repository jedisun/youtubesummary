"""直播运行目录路径工具。"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def build_run_stamp() -> str:
    """生成本次直播运行的时间戳。"""

    return time.strftime("%Y%m%d-%H%M%S", time.localtime())


def sanitize_path_token(value: str) -> str:
    """清洗路径片段，避免特殊字符影响目录命名。"""

    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-._")
    return cleaned or "unknown"


def youtube_video_id(url: str) -> str:
    """尽量从 YouTube URL 中提取稳定的视频 ID。"""

    parsed = urlparse(url)
    if parsed.netloc in {"www.youtube.com", "youtube.com", "m.youtube.com"}:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if video_id:
            return sanitize_path_token(video_id)
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            return sanitize_path_token(path_parts[-1])
    if parsed.netloc == "youtu.be":
        video_id = parsed.path.strip("/")
        if video_id:
            return sanitize_path_token(video_id)
    return sanitize_path_token(url)


def build_run_id(url: str, stamp: str) -> str:
    """生成单次直播运行目录名。

    `stamp` 必须由入口脚本统一生成并传入，避免同一次运行中出现多个时间基准。
    """

    return f"{stamp}_{youtube_video_id(url)}"


@dataclass
class LiveRunLayout:
    """单次直播运行目录布局。"""

    run_dir: Path
    chunk_dir: Path
    log_dir: Path
    state_dir: Path
    transcript_dir: Path
    report_dir: Path

    @property
    def state_file(self) -> Path:
        return self.state_dir / "chunks_state.json"

    @property
    def transcript_file(self) -> Path:
        return self.transcript_dir / "live.transcript.txt"

    @property
    def window_report_file(self) -> Path:
        return self.report_dir / "live.windows.md"

    @property
    def summary_file(self) -> Path:
        return self.report_dir / "live.summary.md"

    @property
    def summary_state_file(self) -> Path:
        return self.state_dir / "summary_state.json"

    @property
    def capture_result_file(self) -> Path:
        return self.log_dir / "capture_result.json"

    @property
    def ffmpeg_log_file(self) -> Path:
        return self.log_dir / "ffmpeg.log"


def live_run_layout(run_dir: Path) -> LiveRunLayout:
    """构造直播运行目录布局。"""

    return LiveRunLayout(
        run_dir=run_dir,
        chunk_dir=run_dir / "chunks",
        log_dir=run_dir / "logs",
        state_dir=run_dir / "state",
        transcript_dir=run_dir / "transcripts",
        report_dir=run_dir / "reports",
    )


def ensure_live_run_layout(run_dir: Path) -> LiveRunLayout:
    """确保直播运行目录存在。"""

    layout = live_run_layout(run_dir)
    for path in (
        layout.run_dir,
        layout.chunk_dir,
        layout.log_dir,
        layout.state_dir,
        layout.transcript_dir,
        layout.report_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
    return layout
