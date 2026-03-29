"""Chunk 状态存储。

这里实现直播 chunk 的最小持久化状态表，用于：
1. 记录每个 chunk 的处理状态
2. 支持最旧优先领取
3. 支持处理中断的超时恢复
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


CHUNK_STATUS_WRITING = "writing"
CHUNK_STATUS_COMPLETED = "completed"
CHUNK_STATUS_PROCESSING = "processing"
CHUNK_STATUS_PROCESSED = "processed"
CHUNK_STATUS_INTERRUPTED = "interrupted"
CHUNK_STATUS_FAILED = "failed"


def utc_now_iso() -> str:
    """返回 UTC ISO 时间，便于跨进程比较。"""

    return datetime.now(timezone.utc).isoformat()


def parse_iso8601(value: str) -> datetime:
    """解析 ISO 时间。"""

    return datetime.fromisoformat(value)


@dataclass
class ChunkRecord:
    """单个 chunk 的状态记录。"""

    chunk_id: str
    chunk_path: str
    created_at: str
    status: str
    updated_at: str
    retry_count: int
    error: str | None
    segment_count: int = 0
    char_count: int = 0
    transcript_status: str | None = None
    summary_eligible: bool = False


class ChunkStateStore:
    """基于 JSON 文件的最小状态表。

    第一版优先保证状态可恢复和可读，不引入数据库。
    """

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def load_records(self) -> list[ChunkRecord]:
        """读取全部 chunk 状态。"""

        if not self.state_path.exists():
            return []
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        return [ChunkRecord(**item) for item in payload.get("chunks", [])]

    def save_records(self, records: list[ChunkRecord]) -> None:
        """写回全部 chunk 状态。

        使用临时文件替换，避免中途写坏状态文件。
        """

        payload = {"chunks": [asdict(record) for record in records]}
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.state_path)

    def upsert_record(self, record: ChunkRecord) -> None:
        """新增或更新一条 chunk 状态。"""

        records = self.load_records()
        for index, current in enumerate(records):
            if current.chunk_id == record.chunk_id:
                records[index] = record
                self.save_records(records)
                return
        records.append(record)
        records.sort(key=lambda item: item.chunk_id)
        self.save_records(records)

    def mark_writing(self, chunk_id: str, chunk_path: Path) -> ChunkRecord:
        """标记某个 chunk 开始写入。"""

        now = utc_now_iso()
        record = ChunkRecord(
            chunk_id=chunk_id,
            chunk_path=str(chunk_path),
            created_at=now,
            status=CHUNK_STATUS_WRITING,
            updated_at=now,
            retry_count=0,
            error=None,
        )
        self.upsert_record(record)
        return record

    def mark_completed(self, chunk_id: str) -> ChunkRecord:
        """标记某个 chunk 已写完。"""

        record = self.require_record(chunk_id)
        record.status = CHUNK_STATUS_COMPLETED
        record.updated_at = utc_now_iso()
        record.error = None
        self.upsert_record(record)
        return record

    def lease_oldest_completed(self) -> ChunkRecord | None:
        """领取最旧的 completed chunk，并标记为 processing。"""

        records = self.load_records()
        for record in records:
            if record.status == CHUNK_STATUS_COMPLETED:
                record.status = CHUNK_STATUS_PROCESSING
                record.updated_at = utc_now_iso()
                self.save_records(records)
                return record
        return None

    def heartbeat(self, chunk_id: str) -> ChunkRecord:
        """刷新 processing chunk 的更新时间。"""

        record = self.require_record(chunk_id)
        record.updated_at = utc_now_iso()
        self.upsert_record(record)
        return record

    def mark_processed(
        self,
        chunk_id: str,
        segment_count: int = 0,
        char_count: int = 0,
        transcript_status: str | None = None,
        summary_eligible: bool = False,
    ) -> ChunkRecord:
        """标记 chunk 处理完成，并写入转写统计结果。"""

        record = self.require_record(chunk_id)
        record.status = CHUNK_STATUS_PROCESSED
        record.updated_at = utc_now_iso()
        record.error = None
        record.segment_count = segment_count
        record.char_count = char_count
        record.transcript_status = transcript_status
        record.summary_eligible = summary_eligible
        self.upsert_record(record)
        return record

    def mark_failed(self, chunk_id: str, error: str) -> ChunkRecord:
        """标记 chunk 处理失败。"""

        record = self.require_record(chunk_id)
        record.status = CHUNK_STATUS_FAILED
        record.updated_at = utc_now_iso()
        record.retry_count += 1
        record.error = error
        self.upsert_record(record)
        return record

    def requeue(self, chunk_id: str) -> ChunkRecord:
        """将失败或中断的 chunk 重新放回 completed。"""

        record = self.require_record(chunk_id)
        record.status = CHUNK_STATUS_COMPLETED
        record.updated_at = utc_now_iso()
        self.upsert_record(record)
        return record

    def recover_interrupted(self, timeout_seconds: int) -> list[ChunkRecord]:
        """把长时间停留在 processing 的 chunk 标记为 interrupted。"""

        now = datetime.now(timezone.utc)
        records = self.load_records()
        recovered: list[ChunkRecord] = []
        changed = False
        for record in records:
            if record.status != CHUNK_STATUS_PROCESSING:
                continue
            updated_at = parse_iso8601(record.updated_at)
            elapsed = (now - updated_at).total_seconds()
            if elapsed <= timeout_seconds:
                continue
            record.status = CHUNK_STATUS_INTERRUPTED
            record.updated_at = utc_now_iso()
            record.error = "processing timeout/interrupted"
            recovered.append(record)
            changed = True
        if changed:
            self.save_records(records)
        return recovered

    def require_record(self, chunk_id: str) -> ChunkRecord:
        """读取单条状态，不存在则报错。"""

        for record in self.load_records():
            if record.chunk_id == chunk_id:
                return record
        raise KeyError(f"Chunk record not found: {chunk_id}")


class ProcessingHeartbeat:
    """独立定时器，用于在 processing 期间周期性刷新 updated_at。"""

    def __init__(self, store: ChunkStateStore, chunk_id: str, interval_seconds: int = 10) -> None:
        self.store = store
        self.chunk_id = chunk_id
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """启动后台刷新线程。"""

        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止后台刷新线程。"""

        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 1)

    def _run(self) -> None:
        """定期刷新 chunk 的 updated_at。"""

        while not self._stop_event.wait(self.interval_seconds):
            self.store.heartbeat(self.chunk_id)
