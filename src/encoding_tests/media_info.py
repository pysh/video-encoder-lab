"""Сведения о видеофайлах и frameserver-скриптах.

Порт функций ``Get-VideoInfo`` / ``Get-VideoScriptInfo`` / ``Get-VideoStats`` из
``Modules/Get-VideoInfo.ps1`` и ``Modules/Utilities.psm1``.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("encoding_tests")

__all__ = [
    "FileType",
    "ScriptInfo",
    "VideoStats",
    "get_frame_rate",
    "get_script_info",
    "get_video_file_type",
    "get_video_stats",
    "parse_frame_rate",
]

DEFAULT_FRAME_RATE = 25.0

_VS_INFO_FPS = re.compile(r"FPS:\s*([\d/]+(?:\.\d+)?)")
_VS_INFO_FRAMES = re.compile(r"Frame Count:\s*(\d+)", re.IGNORECASE)
_VS_INFO_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", re.IGNORECASE)
_FRACTION = re.compile(r"^(\d+)/(\d+)$")


class FileType:
    """Тип источника по расширению (аналог ``Get-VideoFileType``)."""

    VAPOURSYNTH = "VapourSynth"
    AVISYNTH = "AviSynth"
    VIDEO = "Video"


@dataclass(frozen=True)
class ScriptInfo:
    """Информация о видеофайле или frameserver-скрипте."""

    path: Path
    duration: float = 0.0
    frame_count: int | None = None
    frame_rate: float = 0.0
    width: int = 0
    height: int = 0
    raw: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VideoStats:
    """Статистика видеофайла (аналог ``Get-VideoStats``)."""

    file_path: Path
    file_name: str
    file_size_bytes: int
    video_data_size_bytes: int
    video_codec_name: str
    resolution_width: int
    resolution_height: int
    frame_rate: float
    frame_rate_num: int
    frame_rate_den: int
    frame_count: int
    frame_count_packets: int
    frame_count_stream: int
    duration_seconds: float
    duration_from_frames: float
    duration_from_packets: float
    bitrate_kbps: float
    pixel_format: str
    bit_depth: str

    @property
    def formatted_duration(self) -> str:
        """Длительность в формате ``чч:мм:сс``."""
        total_seconds = int(self.duration_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def parse_frame_rate(value: str | None) -> float:
    """Преобразует FPS (``24000/1001`` или ``23.976``) в число.

    Raises:
        ValueError: формат FPS не распознан.
    """
    if not value:
        raise ValueError("Пустое значение FPS")
    text = value.strip()
    match = _FRACTION.match(text)
    if match:
        numerator, denominator = int(match.group(1)), int(match.group(2))
        if denominator == 0:
            raise ValueError(f"Нулевой знаменатель FPS: {value}")
        return numerator / denominator
    try:
        return float(text)
    except ValueError as exc:  # pragma: no cover - сообщение ниже
        raise ValueError(f"Некорректный формат FPS: {value}") from exc


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Запускает процесс, возвращая объединённый stdout/stderr в кодировке UTF-8."""
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=False)


def _require_success(result: subprocess.CompletedProcess[str], description: str) -> str:
    """Проверяет код возврата и возвращает stdout."""
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"{description} завершился с кодом {result.returncode}: {details}")
    return result.stdout or ""


def get_video_file_type(path: str | Path) -> str:
    """Определяет тип источника по расширению файла."""
    extension = Path(path).suffix.lower()
    if extension == ".vpy":
        return FileType.VAPOURSYNTH
    if extension == ".avs":
        return FileType.AVISYNTH
    return FileType.VIDEO


def probe_frame_rate(path: str | Path, ffprobe: str = "ffprobe") -> float:
    """Читает ``r_frame_rate`` первого видеопотока через ffprobe."""
    args = [ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1"]
    if get_video_file_type(path) == FileType.AVISYNTH:
        args += ["-f", "avisynth"]
    args.append(str(path))
    result = _run(args)
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"ffprobe не вернул FPS: {(result.stderr or '').strip()}")
    return parse_frame_rate(result.stdout.strip())


def get_frame_rate(
    path: str | Path,
    file_type: str | None = None,
    *,
    ffprobe: str = "ffprobe",
    vspipe: str = "vspipe.exe",
) -> float:
    """Возвращает FPS источника; при неудаче -- ``25.0`` (как в оригинале)."""
    kind = file_type or get_video_file_type(path)
    try:
        if kind == FileType.VAPOURSYNTH:
            result = _run([vspipe, "--info", str(path)])
            output = f"{result.stdout}\n{result.stderr}"
            match = _VS_INFO_FPS.search(output)
            if match:
                return round(parse_frame_rate(match.group(1)), 3)
        else:
            return round(probe_frame_rate(path, ffprobe), 3)
    except Exception as exc:  # noqa: BLE001 - резервное значение частоты по оригиналу
        log.debug("Не удалось определить FPS для %s: %s", path, exc)
    return DEFAULT_FRAME_RATE


def _parse_vspipe_info(output: str) -> dict[str, str]:
    """Разбирает вывод ``vspipe --info`` в плоский словарь (первое значение приоритетнее)."""
    info: dict[str, str] = {}
    for line in output.splitlines():
        match = re.match(r"^\s*([^:]+):\s*(.*)$", line)
        if match:
            info.setdefault(match.group(1).strip(), match.group(2).strip())
    return info


def _duration_from_text(value: str | None) -> float:
    """Разбирает длительность ``ч:мм:сс.дд`` из вывода vspipe."""
    if not value:
        return 0.0
    match = _VS_INFO_DURATION.search(value)
    if not match:
        try:
            return float(value)
        except ValueError:
            return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _get_vapoursynth_info(script_path: Path, vspipe: str) -> ScriptInfo:
    """Читает метаданные .vpy скрипта через ``vspipe --info``."""
    result = _run([vspipe, "--info", str(script_path)])
    output = _require_success(result, f"vspipe --info {script_path}")
    info = _parse_vspipe_info(output)
    frame_rate = round(parse_frame_rate(info.get("FPS", "")), 3) if info.get("FPS") else 0.0
    frame_count = int(match.group(1)) if (match := _VS_INFO_FRAMES.search(output)) else None
    duration = _duration_from_text(info.get("Duration"))
    if not duration and frame_count and frame_rate:
        duration = frame_count / frame_rate
    return ScriptInfo(
        path=script_path,
        duration=duration,
        frame_count=frame_count,
        frame_rate=frame_rate,
        width=int(info.get("Format Width", 0) or 0),
        height=int(info.get("Format Height", 0) or 0),
        raw=info,
    )


def _first_stream(ffprobe_json: dict[str, object]) -> dict[str, object]:
    streams = ffprobe_json.get("streams") or []
    if not streams:
        raise RuntimeError("ffprobe не вернул ни одного потока")
    return streams[0]  # type: ignore[index]


def _stream_frame_count(stream: dict[str, object]) -> int | None:
    """Извлекает число кадров из метаданных потока (дешёвые источники)."""
    for key in ("nb_read_packets", "nb_frames"):
        value = stream.get(key)
        if value not in (None, "", "N/A"):
            return int(value)
    tags = stream.get("tags") or {}
    value = tags.get("NUMBER_OF_FRAMES")  # type: ignore[union-attr]
    if value not in (None, "", "N/A"):
        return int(value)
    return None


def _stream_duration(stream: dict[str, object]) -> float:
    """Длительность потока из ``duration`` либо из MKV-тега ``DURATION``."""
    value = stream.get("duration")
    if value not in (None, "", "N/A"):
        return float(value)
    tags = stream.get("tags") or {}
    tag = tags.get("DURATION")  # type: ignore[union-attr]
    if tag:
        hours, minutes, seconds = str(tag).split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return 0.0


def _get_file_info(video_path: Path, ffprobe: str) -> ScriptInfo:
    """Читает метаданные обычного видеофайла через ffprobe."""
    args = [ffprobe, "-v", "error", "-show_entries", "stream", "-show_entries", "format",
            "-of", "json", str(video_path)]
    result = _run(args)
    stream = _first_stream(json.loads(_require_success(result, f"ffprobe {video_path}")))
    frame_rate_text = stream.get("avg_frame_rate") or "0/0"
    if frame_rate_text in ("0/0", ""):
        frame_rate_text = str(stream.get("r_frame_rate") or "0/1")
    frame_rate = round(parse_frame_rate(frame_rate_text), 3)
    frame_count = _stream_frame_count(stream)
    duration = _stream_duration(stream)
    if not duration and frame_count and frame_rate:
        duration = frame_count / frame_rate
    return ScriptInfo(
        path=video_path,
        duration=duration,
        frame_count=frame_count,
        frame_rate=frame_rate,
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        raw=dict(stream),
    )


def get_script_info(path: str | Path, *, ffprobe: str = "ffprobe",
                    vspipe: str = "vspipe.exe") -> ScriptInfo:
    """Возвращает сводную информацию о видеофайле или .vpy/.avs скрипте."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Файл не найден: {target}")
    file_type = get_video_file_type(target)
    if file_type == FileType.VAPOURSYNTH:
        return _get_vapoursynth_info(target, vspipe)
    if file_type == FileType.AVISYNTH:
        frame_rate = get_frame_rate(target, file_type, ffprobe=ffprobe, vspipe=vspipe)
        return ScriptInfo(path=target, frame_rate=frame_rate, raw={"type": file_type})
    return _get_file_info(target, ffprobe)


def _probe_packets(video_path: Path, ffprobe: str) -> tuple[int, float, int]:
    """Считает число пакетов, длительность и суммарный размер видео-пакетов.

    Используется ``csv`` вместо ``json``: для длинных файлов JSON со списком всех
    пакетов занимает десятки мегабайт, а из данных нужны только агрегаты.
    """
    args = [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
            "-show_entries", "packet=pts_time,size", "-of",
            "default=noprint_wrappers=1:nokey=1", str(video_path)]
    result = _run(args)
    output = _require_success(result, f"ffprobe (пакеты) {video_path}")
    count = 0
    total_size = 0
    first_pts: float | None = None
    last_pts = 0.0
    for line in output.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pts_text, _, size_text = line.partition("|")
        count += 1
        total_size += int(size_text or 0)
        try:
            pts = float(pts_text)
        except ValueError:
            continue
        if first_pts is None:
            first_pts = pts
        last_pts = max(last_pts, pts)
    duration = (last_pts - first_pts) if (first_pts is not None and last_pts > first_pts) else 0.0
    return count, duration, total_size


def get_video_stats(video_path: str | Path, *, ffprobe: str = "ffprobe") -> VideoStats:
    """Вычисляет статистику видеофайла (аналог ``Get-VideoStats``)."""
    target = Path(video_path)
    if not target.is_file():
        raise FileNotFoundError(f"Файл не найден: {target}")

    args = [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream",
            "-show_entries", "format=size", "-of", "json", str(target)]
    result = _run(args)
    probe = json.loads(_require_success(result, f"ffprobe {target}"))
    stream = _first_stream(probe)

    frame_rate_text = str(stream.get("r_frame_rate") or "0/1")
    frame_rate = round(parse_frame_rate(frame_rate_text), 3)
    numerator, _, denominator = frame_rate_text.partition("/")
    frame_rate_num = int(numerator)
    frame_rate_den = int(denominator) if denominator else 1

    packet_count, packet_duration, video_data_size = _probe_packets(target, ffprobe)
    stream_frame_count = int(stream.get("nb_read_packets") or 0)
    nb_frames = stream.get("nb_frames")
    frame_count = int(nb_frames) if nb_frames not in (None, "", "N/A") else packet_count

    duration_from_frames = round(frame_count / frame_rate, 3) if frame_rate else 0.0
    duration_value = stream.get("duration")
    duration_seconds = (round(float(duration_value), 3)
                        if duration_value not in (None, "", "N/A") else duration_from_frames)
    bitrate_kbps = (round(video_data_size * 8 / packet_duration / 1024, 2)
                    if packet_duration > 0 else 0.0)

    return VideoStats(
        file_path=target,
        file_name=target.name,
        file_size_bytes=target.stat().st_size,
        video_data_size_bytes=video_data_size,
        video_codec_name=str(stream.get("codec_name") or ""),
        resolution_width=int(stream.get("width") or 0),
        resolution_height=int(stream.get("height") or 0),
        frame_rate=frame_rate,
        frame_rate_num=frame_rate_num,
        frame_rate_den=frame_rate_den,
        frame_count=frame_count,
        frame_count_packets=packet_count,
        frame_count_stream=stream_frame_count,
        duration_seconds=duration_seconds,
        duration_from_frames=duration_from_frames,
        duration_from_packets=round(packet_duration, 3),
        bitrate_kbps=bitrate_kbps,
        pixel_format=str(stream.get("pix_fmt") or ""),
        bit_depth=str(stream.get("bits_per_raw_sample") or ""),
    )
