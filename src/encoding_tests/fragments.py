"""Извлечение равномерных фрагментов из MKV при помощи mkvmerge.

Порт функции ``Copy-VideoFragments`` из ``Modules/Utilities.psm1``.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["FragmentResult", "copy_video_fragments"]


@dataclass(frozen=True)
class FragmentResult:
    """Результат работы ``copy_video_fragments``."""

    output_file: Path
    time_ranges: tuple[str, ...]
    command: str
    total_duration: float
    available_duration: float
    fragment_count: int
    fragment_duration: int


def _format_timespan(seconds: float) -> str:
    """Форматирует секунды как ``чч:мм:сс.дд`` (аналог ``hh\\:mm\\:ss\\.ff``)."""
    total = max(0.0, seconds)
    hours, remainder = divmod(int(total), 3600)
    minutes, secs = divmod(remainder, 60)
    hundredths = round((total - int(total)) * 100)
    if hundredths == 100:  # защита от округления вверх
        hundredths = 99
    return f"{hours % 24:02d}:{minutes:02d}:{secs:02d}.{hundredths:02d}"


def _get_duration(input_file: Path, ffprobe: str) -> float:
    """Длительность контейнера в секундах."""
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(input_file)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Не удалось получить длительность {input_file}: {result.stderr.strip()}")
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"ffprobe не вернул длительность для {input_file}")
    return float(lines[-1])


def _build_time_ranges(total_duration: float, fragment_count: int, fragment_duration: int,
                       skip_start: float, skip_end: float) -> tuple[list[str], float]:
    """Рассчитывает равномерные интервалы фрагментов (секунды -> ``чч:мм:сс.дд``)."""
    if skip_start + skip_end >= total_duration:
        msg = (f"Сумма skip_start ({skip_start}) и skip_end ({skip_end}) не меньше "
               f"общей длительности ({total_duration})")
        raise ValueError(msg)
    available = total_duration - skip_start - skip_end
    if available <= fragment_duration:
        msg = (f"Доступная длительность ({available} c) меньше длительности "
               f"фрагмента ({fragment_duration} c)")
        raise ValueError(msg)

    step = (available - fragment_duration) / (fragment_count - 1) if fragment_count > 1 else 0.0
    ranges = []
    for index in range(fragment_count):
        start = skip_start + min(index * step, available - fragment_duration)
        ranges.append(f"{_format_timespan(start)}-{_format_timespan(start + fragment_duration)}")
    return ranges, available


def copy_video_fragments(
    input_file: str | Path,
    output_file: str | Path,
    *,
    fragment_count: int = 10,
    fragment_duration: int = 12,
    skip_start_seconds: float = 180.0,
    skip_end_seconds: float = 180.0,
    keep_audio: bool = False,
    keep_subtitles: bool = False,
    keep_attachments: bool = False,
    keep_global_tags: bool = False,
    keep_chapters: bool = False,
    mkvmerge: str = "mkvmerge.exe",
    ffprobe: str = "ffprobe",
) -> FragmentResult:
    """Извлекает равномерные фрагменты видео в отдельный MKV.

    Args:
        input_file: исходный MKV.
        output_file: целевой файл семпла.
        fragment_count: число фрагментов.
        fragment_duration: длительность одного фрагмента, секунды.
        skip_start_seconds: отступ от начала файла.
        skip_end_seconds: отступ от конца файла.
        keep_audio: сохранять аудиодорожки.
        keep_subtitles: сохранять субтитры.
        keep_attachments: сохранять вложения.
        keep_global_tags: сохранять глобальные теги.
        keep_chapters: сохранять главы.
        mkvmerge: путь к ``mkvmerge``.
        ffprobe: путь к ``ffprobe``.

    Returns:
        :class:`FragmentResult` с путём результата и списком интервалов.
    """
    source = Path(input_file).resolve()
    destination = Path(output_file)
    if not source.is_file():
        raise FileNotFoundError(f"Файл не найден: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    total_duration = _get_duration(source, ffprobe)
    time_ranges, available_duration = _build_time_ranges(
        total_duration, fragment_count, fragment_duration, skip_start_seconds, skip_end_seconds)

    args = [
        mkvmerge, "--ui-language", "en", "--priority", "lower",
        "--output", str(destination),
        "--split", f"parts:{','.join(time_ranges)}",
    ]
    if not keep_global_tags:
        args.append("--no-global-tags")
    if not keep_chapters:
        args.append("--no-chapters")
    if not keep_audio:
        args.append("--no-audio")
    if not keep_subtitles:
        args.append("--no-subtitles")
    if not keep_attachments:
        args.append("--no-attachments")
    args.append(str(source))

    try:
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=False)
        if result.returncode != 0 or not destination.exists():
            details = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"mkvmerge завершился с кодом {result.returncode}: {details}")
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    return FragmentResult(
        output_file=destination,
        time_ranges=tuple(time_ranges),
        command=subprocess.list2cmdline(args),
        total_duration=total_duration,
        available_duration=available_duration,
        fragment_count=fragment_count,
        fragment_duration=fragment_duration,
    )
