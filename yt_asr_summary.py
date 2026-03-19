#!/usr/bin/env python3
"""兼容旧用法的顶层入口脚本。

该文件保留原有执行方式：`python3 yt_asr_summary.py ...`。
实际逻辑已经迁移到 `src/youtubesummary/`，便于后续继续拆分和测试。
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from youtubesummary.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
