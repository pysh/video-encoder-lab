"""Запуск пакета как модуля: ``python -m encoding_tests``.

Эквивалент вызова ``python run_tests.py``.
"""

from __future__ import annotations

import sys

from encoding_tests.cli import run

if __name__ == "__main__":
    sys.exit(run())
