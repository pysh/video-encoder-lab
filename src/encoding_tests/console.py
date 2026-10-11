"""Цветное консольное логирование.

Полный аналог функции ``Write-Log`` из ``Modules/Utilities.psm1``: тот же формат
строки ``[дата-время] [УРОВЕНЬ] [Категория]\\tсообщение``, те же сокращения и цвета.
Дополнительно записи стандартного модуля :mod:`logging` перенаправляются в этот же
консольный форматтер, чтобы сторонние библиотеки не ломали стиль вывода.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

__all__ = [
    "SUCCESS",
    "VERBOSE",
    "get_logger",
    "setup_logging",
    "write_log",
]

# Дополнительные уровни: ниже DEBUG и между INFO и WARNING.
VERBOSE = 5
SUCCESS = 25

logging.addLevelName(VERBOSE, "VERBOSE")
logging.addLevelName(SUCCESS, "SUCCESS")

#: Синонимы уровней, совместимые с написаниями из PowerShell-версии.
_SEVERITY_TO_LEVEL: dict[str, int] = {
    "debug": logging.DEBUG,
    "verbose": VERBOSE,
    "information": logging.INFO,
    "info": logging.INFO,
    "success": SUCCESS,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "error": logging.ERROR,
}

#: уровень -> (сокращение в скобках, ANSI-цвет).
_LEVEL_STYLE: dict[int, tuple[str, str]] = {
    VERBOSE: ("VRB", "33"),  # DarkYellow
    logging.DEBUG: ("DBG", "90"),  # DarkGray
    logging.INFO: ("INF", "36"),  # Cyan
    SUCCESS: ("OK!", "92"),  # Green
    logging.WARNING: ("WRN", "95"),  # DarkMagenta
    logging.ERROR: ("ERR", "91"),  # Red
}

_LEVEL_TO_SEVERITY: dict[int, str] = {
    VERBOSE: "verbose",
    logging.DEBUG: "debug",
    logging.INFO: "information",
    SUCCESS: "success",
    logging.WARNING: "warning",
    logging.ERROR: "error",
}

LOGGER_NAME = "encoding_tests"
_ANSI_RESET = "\x1b[0m"


def _enable_windows_ansi() -> None:
    """Включает обработку ANSI escape-последовательностей в консоли Windows."""
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    enable_virtual_terminal = 0x0004
    for std_handle in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
        handle = kernel32.GetStdHandle(wintypes.HANDLE(std_handle))
        if not handle or handle == -1:
            continue
        mode = wintypes.DWORD()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | enable_virtual_terminal)


def _use_colors() -> bool:
    """Определяет, можно ли раскрашивать вывод."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


_COLORIZE = False


def format_message(
    message: str,
    severity: str = "information",
    category: str | None = None,
    when: datetime | None = None,
) -> str:
    """Формирует строку лога в формате PowerShell-версии ``Write-Log``."""
    level = _SEVERITY_TO_LEVEL.get(severity.lower(), logging.INFO)
    abbrev, color = _LEVEL_STYLE.get(level, ("INF", "36"))
    stamp = when or datetime.now().astimezone()
    timestamp = f"{stamp:%Y-%m-%d %H:%M:%S}.{stamp.microsecond // 1000:03d}"
    category_part = f" [{category}]" if category else ""
    text = f"[{timestamp}] [{abbrev}]{category_part}\t{message}"
    if _COLORIZE:
        return f"\x1b[{color}m{text}{_ANSI_RESET}"
    return text


def write_log(
    message: str,
    severity: str = "information",
    category: str | None = None,
    *,
    newline: bool = True,
) -> None:
    """Пишет сообщение в консоль.

    Args:
        message: текст сообщения.
        severity: уровень (``debug``/``verbose``/``information``/``info``/
            ``success``/``warning``/``warn``/``error``).
        category: необязательная категория в квадратных скобках.
        newline: если ``False``, перевод строки не добавляется (аналог ``-NoNewLine``).
    """
    stream = sys.stderr if severity.lower() == "error" else sys.stdout
    stream.write(format_message(message, severity, category))
    if newline:
        stream.write("\n")
    stream.flush()


class ConsoleHandler(logging.Handler):
    """Handler, передающий записи :mod:`logging` в формат ``Write-Log``."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            severity = _LEVEL_TO_SEVERITY.get(record.levelno, "information")
            write_log(record.getMessage(), severity, getattr(record, "category", None))
        except Exception:  # noqa: BLE001  # pragma: no cover - лог не должен ронять приложение
            self.handleError(record)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Настраивает корневой логгер проекта и возвращает его."""
    global _COLORIZE
    _enable_windows_ansi()
    _COLORIZE = _use_colors()

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    if not any(isinstance(handler, ConsoleHandler) for handler in logger.handlers):
        handler = ConsoleHandler()
        handler.setLevel(level)
        logger.addHandler(handler)
    return logger


def get_logger() -> logging.Logger:
    """Возвращает логгер проекта (создаёт его при первом обращении)."""
    return logging.getLogger(LOGGER_NAME) or setup_logging()
