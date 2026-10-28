"""Make `pilcrow_qt` and the test harness importable from a checkout: qt/ and qt/tests go on sys.path."""

from __future__ import annotations

import sys
from pathlib import Path

QT_DIR = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
for p in (str(QT_DIR), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
