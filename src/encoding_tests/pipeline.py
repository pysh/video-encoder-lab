"""Пайплайн создания тестовых видеофайлов.

Порт ``Create-TestVideoFiles.ps1``: выбор отрезков, подготовка фреймсерверов,
кодирование всех комбинаций параметров и отчёты (CSV / Markdown).
"""

from __future__ import annotations

import csv
import logging
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .encoders import (
    EncoderConfig,
    encode_one,
    encoder_configs,
    get_encoder_version,
    get_output_file_name,
    get_parameters_string,
    get_test_combinations,
    lossless_encode,
)
from .ffplay import SelectionResult, select_files_with_ffplay
from .ffprobe import probe
from .frame_server import CropParams, create_frame_server_script, get_auto_crop_params

log = logging.getLogger("encoding_tests")

# --- пути инструментов по умолчанию (как в оригинальном скрипте) ---
DEFAULT_VSPIPE = r"X:\Apps\_VideoEncoding\StaxRip\Apps\FrameServer\VapourSynth\vspipe.exe"
DEFAULT_FFMPEG = r"X:\Apps\_VideoEncoding\ffmpeg\ffmpeg.exe"
DEFAULT_FFPROBE = r"X:\Apps\_VideoEncoding\ffmpeg\ffprobe.exe"
DEFAULT_FFPLAY = r"X:\Apps\_VideoEncoding\ffmpeg\ffplay.exe"
DEFAULT_MKVMERGE = r"X:\Apps\_VideoEncoding\StaxRip\Apps\Muxers\Matroska\mkvmerge.exe"
DEFAULT_X265 = r"X:\Apps\_VideoEncoding\StaxRip\Apps\Encoders\x265\x265.exe"
DEFAULT_TEMPLATE = Path(r"d:\PSScripts\Convert-VideoToAV1\Templates\VapourSynth\universal_v2_DoVi.vpy")
DEFAULT_AUTO_CROP = Path(r"d:\PSScripts\Convert-VideoToAV1\Tools\AutoCrop.exe")


@dataclass
class Options:
    """Опции запуска пайплайна (аналог параметров PowerShell-скрипта)."""

    input_files: list[Path] = field(default_factory=list)
    output_dir: Path | None = None
    encoders: list[str] = field(default_factory=lambda: ["SvtAv1", "SvtAv1_VMAF", "SvtAv1EncTritium"])
    auto_crop: bool = False
    crop_params: CropParams = field(default_factory=CropParams)
    parallel: int = 1
    min_file_size_mb: float = 100.0
    lossless_encode: bool = False
    no_lossless: bool = False
    no_encode: bool = False
    force: bool = False
    no_timestamp: bool = False
    duration: float = 30.0
    vspipe: str = DEFAULT_VSPIPE
    ffmpeg: str = DEFAULT_FFMPEG
    ffprobe: str = DEFAULT_FFPROBE
    ffplay: str = DEFAULT_FFPLAY
    mkvmerge: str = DEFAULT_MKVMERGE
    x265: str = DEFAULT_X265
    template_path: Path = DEFAULT_TEMPLATE
    auto_crop_exe: Path = DEFAULT_AUTO_CROP
    interactive: bool = True
    preview: bool = False
    log_level: str = "INFO"
    show_active_tasks: bool = False


@dataclass
class SourceInfo:
    """Подготовленный исходный файл (семпл + фреймсерверы + эталон)."""

    selection: SelectionResult
    index: int
    source_name: str
    crop: CropParams
    vpy_path: Path
    avs_path: Path
    lossless_path: Path
    bit_rate_kbps: float = 0.0
    duration_sec: float = 0.0


@dataclass
class EncodeTask:
    """Одна комбинация «исходник × кодировщик × параметры»."""

    source: SourceInfo
    config: EncoderConfig
    params: dict[str, object]
    output_file: Path
    params_string: str
    status: str = "pending"  # pending | running | done | skipped | failed
    error: str = ""
    output_bit_rate: float = 0.0
    output_duration: float = 0.0
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def key(self) -> str:
        """Уникальный ключ задачи ``имя_файла|кодировщик|параметры``."""
        return f"{self.output_file.name}|{self.config.name}|{self.params_string}"

    @property
    def bitrate_percent(self) -> float:
        """Битрейт результата относительно эталона (``x265 -lossless``), %."""
        base = self.source.bit_rate_kbps
        return (self.output_bit_rate * 100 / base) if base else 0.0


class Pipeline:
    """Управляет полным циклом подготовки, кодирования и отчётов."""

    def __init__(self, options: Options) -> None:
        self.options = options
        self.output_dir = Path(options.output_dir) if options.output_dir else Path.cwd()
        self.file_suffix = "" if options.no_timestamp else f"_{datetime.now().astimezone():%Y%m%d_%H%M%S}"
        self.configs: dict[str, EncoderConfig] = encoder_configs(options.encoders)
        self.sources: list[SourceInfo] = []
        self.tasks: list[EncodeTask] = []
        self.report_files: list[Path] = []
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()
        self._active: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # 1. Сбор и выбор исходников
    # ------------------------------------------------------------------ #

    def collect_input_files(self) -> list[str]:
        """Разворачивает списки файлов/папок в плоский список видеофайлов."""
        video_exts = {".mkv", ".mp4", ".m2ts", ".ts", ".avi", ".webm", ".mov"}
        result: list[str] = []
        for entry in self.options.input_files:
            path = Path(entry)
            if path.is_dir():
                found = sorted(str(p) for p in path.iterdir()
                               if p.suffix.lower() in video_exts and p.is_file())
                result.extend(found)
            elif path.is_file():
                result.append(str(path))
            else:
                log.warning("Файл или папка не найдены: %s", entry)
        return result

    def select_source_videos(self) -> list[SelectionResult]:
        """Выбирает отрезки для кодирования (интерактивно или по умолчанию)."""
        files = self.collect_input_files()
        if not files:
            log.error("Не найдено ни одного входного файла")
            return []
        crop = self.options.crop_params
        return select_files_with_ffplay(
            files,
            auto_crop=self.options.auto_crop,
            crop_params=crop if not self.options.auto_crop else None,
            ffplay_exe=self.options.ffplay,
            ffprobe_exe=self.options.ffprobe,
            default_duration=self.options.duration,
            auto_accept=not self.options.interactive,
            no_preview=not self.options.preview,
        )

    # ------------------------------------------------------------------ #
    # 2. Подготовка исходника: семпл, кроп, фреймсервер, эталон
    # ------------------------------------------------------------------ #

    def prepare_source_info(self, selection: SelectionResult, index: int) -> SourceInfo | None:
        """Создаёт семпл, определяет кроп, пишет фреймсерверы и эталон."""
        opts = self.options
        original = Path(selection.original_path)
        source_name = original.stem
        if selection.duration_seconds <= 0:
            log.warning("[%s] Длительность не определена, пропуск", source_name)
            return None

        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 1. trim-семпл через ffmpeg
        sample_name = f"{source_name}_{int(selection.start_frame)}-{int(selection.end_frame)}_Trim.mkv"
        sample_path = self.output_dir / sample_name
        if not self._make_sample(selection, sample_path):
            log.error("Не удалось создать семпл для %s, пропускаем", source_name)
            return None

        # 2. параметры кадрирования
        crop = get_auto_crop_params(sample_path, auto_crop_exe=opts.auto_crop_exe) \
            if opts.auto_crop else CropParams(*_as_crop_tuple(selection.crop_params))

        # 3. фреймсерверы
        base_script = sample_path.with_suffix("")
        vpy_path, avs_path = create_frame_server_script(
            base_script, sample_path, crop, source_video_path=original,
            template_path=opts.template_path)
        log.info("VapourSynth скрипт: %s", vpy_path)

        # 4. эталонный (lossless) файл
        lossless_path = self._make_lossless(vpy_path, base_script, selection.frame_count)

        # 5. битрейт/длительность эталона
        info = probe(str(lossless_path), opts.ffprobe)
        source = SourceInfo(
            selection=selection,
            index=index,
            source_name=source_name,
            crop=crop,
            vpy_path=vpy_path,
            avs_path=avs_path,
            lossless_path=lossless_path,
            bit_rate_kbps=info.bit_rate_kbps,
            duration_sec=info.duration_sec,
        )
        log.info("[%s] Lossless: %.1f kbps, %.1f сек", source_name,
                 info.bit_rate_kbps, info.duration_sec)
        return source

    def _make_sample(self, selection: SelectionResult, sample_path: Path) -> bool:
        """Нарезает trim-семпл через ``ffmpeg`` (copy, без перекодирования).

        Returns:
            ``True``, если семпл на месте (создан или уже существовал).
        """
        if sample_path.exists() and not self.options.force:
            log.info("Семпл уже существует: %s", sample_path.name)
            return True
        args = [
            self.options.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-ss", f"{selection.start_seconds:.3f}", "-i", selection.original_path,
            "-t", f"{selection.duration_seconds:.3f}", "-map", "0:v",
            "-map", "0:a?", "-map", "0:s?", "-map_metadata", "0",
            "-c", "copy", str(sample_path),
        ]
        log.info("Создание семпла: %s (%.1f-%.1f c)", sample_path.name,
                 selection.start_seconds, selection.start_seconds + selection.duration_seconds)
        try:
            result = subprocess.run(args, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", check=False)
        except OSError as exc:
            log.error("Не удалось запустить ffmpeg: %s", exc)
            return False
        if result.returncode != 0 or not sample_path.exists():
            details = (result.stderr or result.stdout or "").strip()
            log.error("ffmpeg не создал семпл %s: %s", sample_path.name, details)
            return False
        return True

    def _make_lossless(self, vpy_path: Path, base_script: Path, frame_count: int) -> Path:
        """Создаёт lossless-эталон (или пустую заглушку, если он отключён)."""
        if self.options.no_lossless:
            log.info("Lossless кодирование пропущено (--no-lossless)")
            return base_script.with_suffix(".x265")
        if not self.options.lossless_encode:
            log.info("Lossless пропущен (не включён)")
            return base_script.with_suffix(".x265")
        target = base_script.with_suffix(".x265")
        try:
            lossless_encode(vpy_path, target, frame_count, x265_exe=self.options.x265)
        except RuntimeError as exc:
            log.error("Ошибка lossless кодирования: %s", exc)
        return target

    # ------------------------------------------------------------------ #
    # 3. Постановка задач
    # ------------------------------------------------------------------ #

    def build_tasks(self) -> list[EncodeTask]:
        """Создаёт все комбинации «исходник × кодировщик × параметры»."""
        tasks: list[EncodeTask] = []
        for source in self.sources:
            for config in self.configs.values():
                for params in get_test_combinations(config):
                    params_string = get_parameters_string(params)
                    file_name = get_output_file_name(config, params, "", self.file_suffix)
                    tasks.append(EncodeTask(
                        source=source,
                        config=config,
                        params=params,
                        params_string=params_string,
                        output_file=self.output_dir / file_name,
                    ))
        self.tasks = tasks
        return tasks

    # ------------------------------------------------------------------ #
    # 4. Кодирование
    # ------------------------------------------------------------------ #

    def encode_task(self, task: EncodeTask) -> EncodeTask:
        """Кодирует одну задачу (поток-безопасно)."""
        if task.output_file.exists() and task.output_file.stat().st_size > 0:
            task.status = "skipped"
            log.info("Пропуск (уже существует): %s", task.output_file.name)
            return task

        task.status = "running"
        with self._lock:
            self._active[task.key] = task.started_at or time.monotonic()
            task.started_at = self._active[task.key]
        log.info("Старт: %s [%s]", task.output_file.name, task.config.display_name)
        try:
            encode_one(
                config=task.config,
                test_params=task.params,
                input_script=task.source.vpy_path,
                output_file=task.output_file,
                server_type="VapourSynth",
                frame_count=task.source.selection.frame_count,
                vspipe=self.options.vspipe,
                mkvmerge=self.options.mkvmerge,
                ffmpeg=self.options.ffmpeg,
            )
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            task.status = "failed"
            task.error = str(exc)
            log.error("Ошибка %s: %s", task.output_file.name, exc)
        finally:
            task.finished_at = time.monotonic()
            with self._lock:
                self._active.pop(task.key, None)

        if task.status == "running":
            task.status = "done"
            info = probe(str(task.output_file), self.options.ffprobe)
            task.output_bit_rate, task.output_duration = info.bit_rate_kbps, info.duration_sec
            if task.source.bit_rate_kbps:
                log.info("Готово: %s | %.1f kbps (%.2f%% от эталона)", task.output_file.name,
                         task.output_bit_rate, task.bitrate_percent)
            if self.options.show_active_tasks:
                self.print_active_tasks()
        return task

    def run_all_tasks(self) -> None:
        """Выполняет все задачи последовательно или в пуле потоков."""
        pending = [t for t in self.tasks if t.status == "pending"]
        if not pending:
            log.info("Нет задач для кодирования")
            return
        if self.options.parallel <= 1:
            for task in pending:
                self.encode_task(task)
            return

        log.info("Параллельное кодирование в %d потоков", self.options.parallel)
        futures: dict[Future[EncodeTask], EncodeTask] = {}
        self._pool = ThreadPoolExecutor(max_workers=self.options.parallel)
        for task in pending:
            futures[self._pool.submit(self.encode_task, task)] = task
        for future, task in futures.items():
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                task.status = "failed"
                task.error = str(exc)
                log.error("Сбой задачи %s: %s", task.output_file.name, exc)
        if self._pool:
            self._pool.shutdown(wait=True)
            self._pool = None

    def print_active_tasks(self) -> None:
        """Печатает текущие активные задачи с временем выполнения."""
        now = time.monotonic()
        with self._lock:
            active = dict(self._active)
        if not active:
            log.info("Активные задачи: нет")
            return
        lines = [f"Активные задачи ({len(active)}):"]
        lines += [f"  {key.split('|')[0]} — {now - started:.0f} c"
                  for key, started in active.items()]
        log.info("\n".join(lines))

    # ------------------------------------------------------------------ #
    # 5. Отчёты
    # ------------------------------------------------------------------ #

    def generate_report(self) -> None:
        """Печатает сводную таблицу результатов."""
        completed = [t for t in self.tasks if t.status == "done"]
        log.info("=" * 95)
        log.info("ИТОГО: %d файлов, %d успешно, %d с ошибками, %d пропущено",
                 len(self.tasks), len(completed),
                 len([t for t in self.tasks if t.status == "failed"]),
                 len([t for t in self.tasks if t.status == "skipped"]))
        log.info("=" * 95)
        header = (f"{'Encoder':<18} {'Params':<22} {'Bitrate':>10} {'%':>8} "
                  f"{'Duration':>9} {'File':<30}")
        log.info(header)
        log.info("-" * 95)
        for task in completed:
            log.info("%-18s %-22s %9.1fK %7.2f%% %8.1fs %-30s",
                     task.config.display_name, task.params_string,
                     task.output_bit_rate, task.bitrate_percent,
                     task.output_duration, task.output_file.name[:27])
        log.info("=" * 95)
        self.write_csv_report()
        self.write_markdown_report()

    def write_csv_report(self) -> Path:
        """Сохраняет результаты в CSV (аналог ``Export-Csv``)."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_file = self.output_dir / f"test_results{self.file_suffix}.csv"
        fields = ["SourceName", "Encoder", "DisplayName", "TestParams", "OutputFile",
                  "OutputBitrate", "OutputDuration", "OriginalBitrate", "BitratePercent", "Status"]
        with report_file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for task in self.tasks:
                writer.writerow({
                    "SourceName": task.source.source_name,
                    "Encoder": task.config.name,
                    "DisplayName": task.config.display_name,
                    "TestParams": task.params_string,
                    "OutputFile": str(task.output_file),
                    "OutputBitrate": f"{task.output_bit_rate:.1f}",
                    "OutputDuration": f"{task.output_duration:.1f}",
                    "OriginalBitrate": f"{task.source.bit_rate_kbps:.1f}",
                    "BitratePercent": f"{task.bitrate_percent:.1f}",
                    "Status": task.status,
                })
        self.report_files.append(report_file)
        log.info("CSV-отчёт: %s", report_file)
        return report_file

    def write_markdown_report(self) -> Path:
        """Сохраняет результаты в Markdown-таблицу (аналог отчёта из оригинала)."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        report_file = self.output_dir / f"test_results{self.file_suffix}.md"
        rows = []
        for task in self.tasks:
            if task.status != "done":
                continue
            rows.append(f"| {task.source.source_name} | {task.config.display_name} "
                        f"| {task.params_string} | {task.output_bit_rate:.1f} Kbps "
                        f"| {task.output_duration:.1f} s | {task.bitrate_percent:.1f}% "
                        f"| {task.output_file.name} |")
        content = [
            f"# Results {datetime.now().astimezone():%Y-%m-%d %H:%M}",
            "",
            f"## Summary: {len(rows)} files",
            "",
            "| Source | Encoder | Parameters | Bitrate | Duration | vs Lossless | File |",
            "|--------|---------|------------|---------|----------|-------------|------|",
            *rows,
            "",
            "## Legend",
            "- **vs Lossless**: output bitrate as percent of lossless reference bitrate",
            "",
        ]
        report_file.write_text("\n".join(content), encoding="utf-8")
        self.report_files.append(report_file)
        log.info("Markdown-отчёт: %s", report_file)
        return report_file

    def show_file_sizes(self) -> None:
        """Печатает размеры эталонных и результирующих файлов."""
        log.info("\nРазмеры файлов:")
        for source in self.sources:
            if source.lossless_path.exists():
                log.info("  Эталон %-40s %8.1f MB", source.lossless_path.name,
                         source.lossless_path.stat().st_size / 1024 / 1024)
        for task in self.tasks:
            if task.status == "done" and task.output_file.exists():
                log.info("  Результат %-40s %8.1f MB", task.output_file.name,
                         task.output_file.stat().st_size / 1024 / 1024)

    # ------------------------------------------------------------------ #
    # Точка входа
    # ------------------------------------------------------------------ #

    def run(self) -> int:
        """Выполняет весь пайплайн. Возвращает код завершения процесса."""
        opts = self.options
        log.info("=" * 60)
        log.info("СОЗДАНИЕ ТЕСТОВЫХ ВИДЕОФАЙЛОВ")
        log.info("=" * 60)
        log.info("Кодировщики: %s", ", ".join(c.display_name for c in self.configs.values()))
        for config in self.configs.values():
            log.info("Версия %s: %s", config.name, get_encoder_version(config))
        log.info("Параллельно: %d", opts.parallel)
        log.info("Авто-кроп: %s", "да" if opts.auto_crop else f"нет {opts.crop_params}")
        log.info("Выходная папка: %s", self.output_dir)

        selections = self.select_source_videos()
        if not selections:
            log.error("Выбор файлов отменён")
            return 1

        for index, selection in enumerate(selections):
            prepared = self.prepare_source_info(selection, index)
            if prepared:
                self.sources.append(prepared)
        if not self.sources:
            log.error("Не подготовлено ни одного исходника")
            return 1

        self.build_tasks()
        log.info("=" * 60)
        log.info("ВСЕГО ЗАДАЧ: %d (%d исходников × %d кодировщиков)",
                 len(self.tasks), len(self.sources), len(self.configs))
        log.info("=" * 60)

        if opts.no_encode:
            log.info("--no-encode: кодирование пропущено")
        else:
            self.run_all_tasks()

        self.generate_report()
        self.show_file_sizes()
        log.info("Готово!")
        return 0


def _as_crop_tuple(crop: object) -> tuple[int, int, int, int]:
    """Приводит объект кропа к кортежу ``(left, top, right, bottom)``."""
    if isinstance(crop, CropParams):
        return (crop.left, crop.top, crop.right, crop.bottom)
    if isinstance(crop, (tuple, list)) and len(crop) == 4:
        return tuple(int(x) for x in crop)  # type: ignore[return-value]
    return (0, 0, 0, 0)


def main(argv: list[str] | None = None) -> int:
    """Точка входа модуля (разбор аргументов делегируется :mod:`encoding_tests.cli`)."""
    from .cli import run  # локальный импорт: cli зависит от этого модуля

    return run(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
