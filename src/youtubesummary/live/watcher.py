"""Chunk 轮询器。

第一版 watcher 只实现：
1. 扫描 chunk 目录
2. 把新 chunk 注册到状态表
3. 把 completed chunk 领取为 processing
4. 通过回调模拟处理并写回 processed / failed
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .state import (
    CHUNK_STATUS_COMPLETED,
    CHUNK_STATUS_FAILED,
    CHUNK_STATUS_INTERRUPTED,
    ChunkRecord,
    ChunkStateStore,
    ProcessingHeartbeat,
)


ChunkHandler = Callable[[ChunkRecord], object]


def chunk_id_from_path(chunk_path: Path) -> str:
    """由 chunk 文件名生成 chunk_id。"""

    return chunk_path.stem


def register_completed_chunks(chunk_dir: Path, store: ChunkStateStore) -> list[ChunkRecord]:
    """把目录中尚未入表的 chunk 注册为 completed。"""

    existing_ids = {record.chunk_id for record in store.load_records()}
    registered: list[ChunkRecord] = []
    for chunk_path in sorted(chunk_dir.glob("*.wav")):
        chunk_id = chunk_id_from_path(chunk_path)
        if chunk_id in existing_ids:
            continue
        store.mark_writing(chunk_id=chunk_id, chunk_path=chunk_path)
        registered.append(store.mark_completed(chunk_id))
    return registered


def requeue_retryable_chunks(store: ChunkStateStore) -> list[ChunkRecord]:
    """把 interrupted / failed 的 chunk 重新放回 completed。

    第一版为了便于本地验证，不加复杂重试策略；后续再引入最大重试次数。
    """

    retried: list[ChunkRecord] = []
    for record in store.load_records():
        if record.status not in {CHUNK_STATUS_INTERRUPTED, CHUNK_STATUS_FAILED}:
            continue
        retried.append(store.requeue(record.chunk_id))
    return retried


def watch_chunks(
    chunk_dir: Path,
    store: ChunkStateStore,
    handler: ChunkHandler,
    poll_seconds: int = 2,
    processing_timeout: int = 30,
    heartbeat_interval: int = 10,
    once: bool = False,
) -> None:
    """轮询 chunk 目录并推进最小状态机。"""

    while True:
        store.recover_interrupted(timeout_seconds=processing_timeout)
        register_completed_chunks(chunk_dir=chunk_dir, store=store)
        requeue_retryable_chunks(store=store)

        record = store.lease_oldest_completed()
        if record is None:
            if once:
                return
            time.sleep(poll_seconds)
            continue

        heartbeat = ProcessingHeartbeat(store=store, chunk_id=record.chunk_id, interval_seconds=heartbeat_interval)
        heartbeat.start()
        try:
            result = handler(record)
            segment_count = int(getattr(result, "segment_count", 0) or 0)
            char_count = int(getattr(result, "char_count", 0) or 0)
            transcript_status = getattr(result, "transcript_status", None)
            summary_eligible = bool(getattr(result, "summary_eligible", False))
            store.mark_processed(
                record.chunk_id,
                segment_count=segment_count,
                char_count=char_count,
                transcript_status=transcript_status,
                summary_eligible=summary_eligible,
            )
        except Exception as exc:  # pragma: no cover - 第一版先保底捕获
            store.mark_failed(record.chunk_id, error=str(exc))
        finally:
            heartbeat.stop()

        if once:
            return
