#!/usr/bin/env python3
"""Stable command-line entry point for ARIS LitDB."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from litdb.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
