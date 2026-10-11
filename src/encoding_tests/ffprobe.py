"""Чтение метаданных медиафайлов через ``ffprobe``.

Порт функций ``Get-VideoDuration`` и ``Get-VideoBitrate``
из ``Create-TestVideoFiles.ps1``.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

__all__ = ["MediaInfo", "get_video_bitrate", "get_video_duration", "probe"]

TIMEOUT_SEC = 600


class ProbeError(RuntimeError):
    """Ошибка вызова ``ffprobe``."""


@dataclass(frozen=True)
class MediaInfo:
    """Метаданные медиафайла в секундах и килобитах в секунду."""

    duration_sec: float
    bit_rate_kbps: float

    @property
    def is_empty(self) -> bool:
        """``True``, если ни длительность, ни битрейт не определены."""
        return self.duration_sec == 0 and self.bit_rate_kbps == 0


def _to_float(value: object, default: float = 0.0) -> float:
    """Безопасное приведение строкового значения к float."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _to_int(value: object, default: int = 0) -> int:
    """Безопасное приведение строкового значения к int."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def probe(file_path: str, ffprobe_exe: str = "ffprobe.exe") -> MediaInfo:
    """Возвращает длительность и общий битрейт файла.

    Args:
        file_path: путь к медиафайлу.
        ffprobe_exe: путь к исполняемому файлу ``ffprobe``.

    Returns:
        :class:`MediaInfo`. При ошибке возвращает пустой объект (как оригинал).
    """
    try:
        result = subprocess.run(
            [ffprobe_exe, "-hide_banner", "-loglevel", "error", "-print_format", "json",
             "-show_format", "-show_streams", file_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_SEC, check=False)
        if result.returncode != 0:
            return MediaInfo(0.0, 0.0)
        data = json.loads(result.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return MediaInfo(0.0, 0.0)

    fmt = data.get("format", {}) or {}
    streams = data.get("streams") or []

    duration = _to_float(fmt.get("duration"))
    if duration <= 0:
        # Длительность не записана в заголовок контейнера — берём из потока.
        duration = max((_to_float(s.get("duration")) for s in streams), default=0.0)

    bit_rate = _to_int(fmt.get("bit_rate"))
    if bit_rate <= 0:
        # Контейнеры без seek-индекса (например, Matroska после pipe-кодирования)
        # не пишут суммарный битрейт: считаем из размера файла, иначе суммируем
        # битрейты потоков.
        size = _to_int(fmt.get("size"))
        if size > 0 and duration > 0:
            bit_rate = int(size * 8 / duration)
        else:
            bit_rate = sum(_to_int(s.get("bit_rate")) for s in streams)

    return MediaInfo(duration_sec=duration, bit_rate_kbps=bit_rate / 1000)


def get_video_duration(file_path: str, ffprobe_exe: str = "ffprobe.exe") -> float:
    """Возвращает длительность видео в секундах (0 при ошибке)."""
    return probe(file_path, ffprobe_exe).duration_sec


def get_video_bitrate(file_path: str, ffprobe_exe: str = "ffprobe.exe") -> float:
    """Возвращает общий битрейт видео в Кбит/с (0 при ошибке)."""
    return probe(file_path, ffprobe_exe).bit_rate_kbps
