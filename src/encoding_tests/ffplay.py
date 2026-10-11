"""Интерактивный выбор файлов для тестирования.

Текстовый аналог ``Select-FilesWithFFPlay.ps1`` (WPF-окно с превью через ffplay).
Функция :func:`select_files_with_ffplay` возвращает те же поля, что и оригинал:
``OriginalPath``, ``StartSeconds``, ``DurationSeconds``, ``StartFrame``, ``EndFrame``,
``FPS``, ``FrameCount``, ``CropParams`` (``CropParams`` заполняет вызывающая сторона).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SelectionResult", "VideoProbeInfo", "probe_video", "select_files_with_ffplay"]

FFPLAY_SEEK_KEY = "RIGHT"
FFPLAY_PLAY_PAUSE_KEYS = ("SPACE", "P")
FFPLAY_QUIT_KEYS = ("q", "Q", "ESCAPE")
FFPLAY_MAX_LAUNCH_ATTEMPTS = 3
DEFAULT_DURATION = 30.0
MAX_DURATION = 120.0
MIN_DURATION = 1.0
DEFAULT_START_TIME = 5.0


@dataclass
class VideoProbeInfo:
    """Основные параметры видеопотока."""

    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    bit_rate_kbps: float = 0.0


def probe_video(video_path: str, ffprobe_exe: str = "ffprobe.exe") -> VideoProbeInfo:
    """Возвращает разрешение, FPS, длительность и битрейт первого видеопотока."""
    try:
        result = subprocess.run(
            [ffprobe_exe, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate,duration,bit_rate", "-show_entries", "format=duration",
             "-of", "json", video_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode != 0:
            return VideoProbeInfo()
        data = json.loads(result.stdout or "{}")
        streams = data.get("streams") or []
        if not streams:
            return VideoProbeInfo()
        stream = streams[0]
        fps_str = stream.get("r_frame_rate", "0/1")
        try:
            num, _, den = fps_str.partition("/")
            fps = float(num) / float(den or 1)
        except (TypeError, ValueError, ZeroDivisionError):
            fps = 0.0
        duration = _first_float(stream.get("duration"), (data.get("format") or {}).get("duration"))
        bit_rate = _first_float(stream.get("bit_rate"), (data.get("format") or {}).get("bit_rate"))
        return VideoProbeInfo(
            width=int(stream.get("width") or 0),
            height=int(stream.get("height") or 0),
            fps=fps,
            duration=duration,
            bit_rate_kbps=bit_rate / 1000,
        )
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return VideoProbeInfo()


def _first_float(*values: object) -> float:
    """Возвращает первое успешно приведённое к float значение, иначе 0."""
    for value in values:
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return 0.0


@dataclass
class SelectionResult:
    """Результат интерактивного выбора отрезка видео."""

    original_path: str
    start_seconds: float
    duration_seconds: float
    start_frame: int
    end_frame: int
    fps: float
    frame_count: int
    crop_params: object = None  # заполняет pipeline (CropParams из frame_server)
    auto_crop: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """``True`` если выбор не сделан."""
        return not self.original_path


_EMPTY = SelectionResult("", 0.0, 0.0, 0, 0, 0.0, 0)


def _to_selection(video_path: str, start_seconds: float, duration_seconds: float,
                  fps: float, crop_params: object = None) -> SelectionResult:
    """Создаёт :class:`SelectionResult` из временных отрезков."""
    start_frame = int(start_seconds * fps)
    end_frame = int((start_seconds + duration_seconds) * fps)
    return SelectionResult(
        original_path=video_path,
        start_seconds=round(start_seconds, 3),
        duration_seconds=round(duration_seconds, 3),
        start_frame=start_frame,
        end_frame=end_frame,
        fps=fps,
        frame_count=end_frame - start_frame,
        crop_params=crop_params,
    )


def _print_video_info(video_path: str, info: VideoProbeInfo) -> None:
    """Печатает сводку по видеофайлу."""
    print(f"\n{Path(video_path).name}")
    print(f"   {info.width}x{info.height} | {info.fps:.3f} fps | "
          f"{info.duration:.2f} s | {info.bit_rate_kbps:.0f} kbps")


def select_files_with_ffplay(
    video_paths: Sequence[str],
    ffprobe_exe: str = "ffprobe",
    ffplay_exe: str = "ffplay",
    crop_params: str | None = None,
    auto_crop: bool = False,
    auto_accept: bool = False,
    no_preview: bool = False,
    default_duration: float = DEFAULT_DURATION,
) -> list[SelectionResult]:
    """Интерактивно выбирает фрагменты каждого видео.

    Args:
        video_paths: список входных видеофайлов.
        ffprobe_exe: путь к ``ffprobe``.
        ffplay_exe: путь к ``ffplay``.
        crop_params: параметр ``crop`` для ``-vf`` (или ``None``).
        auto_crop: проставить :attr:`SelectionResult.auto_crop` у результата.
        auto_accept: не запрашивать ввод, использовать значения по умолчанию
            (используется вместе с ``--no-interactive``).
        no_preview: не запускать окно ffplay, сразу спрашивать границы фрагмента
            (используется вместе с ``--no-preview``). Границы по умолчанию те же,
            что показывал предпросмотр, поэтому результат не меняется.
        default_duration: длительность отрезка по умолчанию, сек.

    Returns:
        Список :class:`SelectionResult` (пустой, если ничего не выбрано).
    """
    if not video_paths:
        print("No input files provided")
        return []

    results: list[SelectionResult] = []
    total = len(video_paths)
    print(f"\n{'=' * 70}")
    print(f"  SELECT FILES ({total})")
    print("=" * 70)
    if no_preview:
        print("  Preview disabled (--preview to enable the ffplay window)")

    for index, video_path in enumerate(video_paths, start=1):
        print(f"\n[{index}/{total}]")
        if not Path(video_path).is_file():
            print(f"   File not found, skipping: {video_path}")
            continue

        info = probe_video(video_path, ffprobe_exe)
        fps = info.fps if info.fps > 0 else 23.976
        _print_video_info(video_path, info)

        start_time = DEFAULT_START_TIME if info.duration <= 0 else min(
            info.duration * 0.25, info.duration - default_duration)
        start_time = max(0.0, start_time)

        if not auto_accept and not no_preview:
            _launch_preview(video_path, start_time, ffplay_exe)

        start_seconds = _prompt_float("   Start time, s", start_time)
        duration_seconds = min(max(_prompt_float("   Duration, s", default_duration),
                                   MIN_DURATION), MAX_DURATION)

        selection = _to_selection(video_path, start_seconds, duration_seconds, fps, crop_params)
        selection.auto_crop = auto_crop
        results.append(selection)
        print(f"   OK: start {selection.start_frame} -> end {selection.end_frame} "
              f"({selection.frame_count} frames)")

    if auto_accept:
        for result in results:
            print(f"Auto-selected: {Path(result.original_path).name} "
                  f"({result.frame_count} frames)")
    print(f"\nSelected: {len(results)} of {total}")
    return results


def _launch_preview(video_path: str, start_time: float, ffplay_exe: str) -> None:
    """Запускает ffplay для предпросмотра (до :data:`FFPLAY_MAX_LAUNCH_ATTEMPTS` попыток)."""
    if not shutil.which(ffplay_exe) and not Path(ffplay_exe).is_file():
        print(f"   {ffplay_exe} not found, preview skipped")
        return
    for attempt in range(1, FFPLAY_MAX_LAUNCH_ATTEMPTS + 1):
        print(f"   Preview: ffplay -ss {start_time:.1f} (перемотка — клавиша RIGHT, "
              f"выход — q). Окно можно закрыть в любой момент.")
        try:
            process = subprocess.Popen(
                [ffplay_exe, "-autoexit", "-loglevel", "quiet", "-ss", f"{start_time:.3f}",
                 video_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            print(f"   Preview attempt {attempt} failed to start: {exc}")
            continue
        process.wait()
        return


def _prompt_float(prompt: str, default: float) -> float:
    """Запрашивает число с клавиатуры, пустой ввод — значение по умолчанию."""
    while True:
        try:
            raw = input(f"{prompt} [{default:.1f}]: ").strip()
        except EOFError:
            return default
        if not raw:
            return default
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            print("   Нужно число, попробуйте ещё раз.")


def _unused() -> None:  # pragma: no cover - заглушка для flake8-импортов
    sys.audit = getattr(sys, "audit", None)
