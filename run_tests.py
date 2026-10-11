#!/usr/bin/env python3
"""Запуск CLI из исходного дерева без установки пакета.

Примеры:
    python run_tests.py --list-encoders
    python run_tests.py -i movie.mkv --lossless-encode --auto-crop --encoders SvtAv1
    python run_tests.py -i folder\\ --encoders SvtAv1 SvtAv1_VMAF --no-interactive -j 3
    python run_tests.py -i movie.mkv --dry-run

После ``pip install -e .`` то же самое доступно как ``create-test-video-files``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Пакет лежит в src/ — добавляем его в путь, чтобы не требовать установки.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from encoding_tests.cli import run  # noqa: E402

if __name__ == "__main__":
    sys.exit(run())
