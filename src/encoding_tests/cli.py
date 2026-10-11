"""Интерфейс командной строки пайплайна тестового кодирования.

Аналог блока ``param(...)`` и ``#region Banner`` из ``Create-TestVideoFiles.ps1``.
Точка входа: :func:`run` (используется и ``run_tests.py``, и ``python -m encoding_tests``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .console import setup_logging
from .encoders import (
    DEFAULT_ENCODERS,
    ENCODER_CONFIGS,
    encoder_configs,
    get_encoder_version,
    get_test_combinations,
)
from .frame_server import CropParams
from .pipeline import (
    DEFAULT_AUTO_CROP,
    DEFAULT_TEMPLATE,
    Options,
    Pipeline,
)

__all__ = ["build_parser", "options_from_args", "run"]

PROG = "create-test-video-files"
DESCRIPTION = "Создание тестовых видеофайлов разными кодировщиками и сравнение битрейтов."
EPILOG = """\
примеры:
  # два файла, lossless-эталон, авто-кроп, два SVT-AV1 пресета
  python run_tests.py -i movie.mkv --lossless-encode --auto-crop --encoders SvtAv1

  # пачка из папки, без интерактивного выбора, параллельно в 3 потока
  python run_tests.py -i folder\\ --encoders SvtAv1 SvtAv1_VMAF --no-interactive -j 3

  # только подготовка (семпл + .vpy + lossless), без кодирования
  python run_tests.py -i movie.mkv --lossless-encode --no-encode
"""

#: Формат вывода :option:`--list-encoders` / :option:`--show-encoders`.
ENCODER_TABLE_HEADER = (f"{'Имя':<18} {'Отображаемое имя':<24} {'Комбинаций':>10} "
                        f"{'Версия':<16} {'Расширение':<10} {'Параметры'}")


def _positive_int(value: str) -> int:
    """argparse-тип: целое число >= 1."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"значение должно быть >= 1, получено {number}")
    return number


def _non_negative_float(value: str) -> float:
    """argparse-тип: неотрицательное число."""
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError(f"значение должно быть >= 0, получено {number}")
    return number


def _crop_params(value: str) -> CropParams:
    """argparse-тип: ``left,top,right,bottom``."""
    parts = [part.strip() for part in value.replace(";", ",").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "ожидается 4 числа через запятую: left,top,right,bottom")
    try:
        left, top, right, bottom = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"не числа в значении {value!r}") from exc
    if min(left, top, right, bottom) < 0:
        raise argparse.ArgumentTypeError("значения кадрирования не могут быть отрицательными")
    return CropParams(left=left, top=top, right=right, bottom=bottom)


def build_parser() -> argparse.ArgumentParser:
    """Создаёт парсер аргументов командной строки."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-i", "--input", nargs="+", required=False, metavar="PATH",
                        help="входные видеофайлы или папки с ними")
    parser.add_argument("-o", "--output-dir", type=Path, default=None,
                        help="папка результатов (по умолчанию: папка первого входного файла)")
    parser.add_argument("-e", "--encoders", nargs="+", metavar="NAME", default=None,
                        help=f"коды кодировщиков: {', '.join(ENCODER_CONFIGS)} "
                             f"(по умолчанию: {', '.join(DEFAULT_ENCODERS)})")
    parser.add_argument("--auto-crop", action="store_true",
                        help="определить кадрирование автоматически через AutoCrop.exe")
    parser.add_argument("--crop", type=_crop_params, default=CropParams(),
                        metavar="L,T,R,B",
                        help="кадрирование left,top,right,bottom (по умолчанию 0,0,0,0)")
    parser.add_argument("-j", "--parallel", type=_positive_int, default=1,
                        help="число одновременных задач кодирования (по умолчанию 1)")
    parser.add_argument("--min-file-size", type=_non_negative_float, default=100.0, metavar="MB",
                        help="минимальный размер файла-кандидата в MB (по умолчанию 100)")
    parser.add_argument("--lossless-encode", action="store_true",
                        help="создать lossless-эталон через x265 перед тестами")
    parser.add_argument("--no-lossless", action="store_true",
                        help="полностью пропустить lossless-эталон")
    parser.add_argument("--no-encode", action="store_true",
                        help="только подготовка (семплы, скрипты, эталон), без кодирования")
    parser.add_argument("--no-interactive", action="store_true",
                        help="не запрашивать выбор отрезков, использовать значения по умолчанию")
    parser.add_argument("--preview", action="store_true",
                        help="показывать окно предпросмотра ffplay при выборе отрезков "
                             "(по умолчанию выключено)")
    parser.add_argument("--no-preview", action="store_true",
                        help=argparse.SUPPRESS)  # deprecated: предпросмотр уже выключен по умолчанию
    parser.add_argument("--force", action="store_true",
                        help="пересоздавать семпл даже если он уже существует")
    parser.add_argument("--no-timestamp", action="store_true",
                        help="не добавлять метку времени в имена выходных файлов")
    parser.add_argument("--duration", type=_non_negative_float, default=30.0, metavar="SEC",
                        help="длительность тестового отрезка, сек (по умолчанию 30)")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help=f"шаблон .vpy (по умолчанию: {DEFAULT_TEMPLATE})")
    parser.add_argument("--autocrop-exe", type=Path, default=DEFAULT_AUTO_CROP,
                        help=f"путь к AutoCrop.exe (по умолчанию: {DEFAULT_AUTO_CROP})")

    tools = parser.add_argument_group("внешние утилиты")
    tools.add_argument("--vspipe", default=None, help="путь к vspipe.exe")
    tools.add_argument("--ffmpeg", default=None, help="путь к ffmpeg.exe")
    tools.add_argument("--ffprobe", default=None, help="путь к ffprobe.exe")
    tools.add_argument("--ffplay", default=None, help="путь к ffplay.exe")
    tools.add_argument("--mkvmerge", default=None, help="путь к mkvmerge.exe")
    tools.add_argument("--x265", default=None, help="путь к x265.exe")

    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "VERBOSE", "INFO", "WARNING", "ERROR"],
                        help="уровень подробности лога (по умолчанию INFO)")
    parser.add_argument("--show-active-tasks", action="store_true",
                        help="печатать список активных задач после каждой завершённой")
    parser.add_argument("--list-encoders", "--show-encoders", action="store_true",
                        dest="list_encoders",
                        help="показать список доступных кодировщиков, их комбинации и версии, "
                             "затем выйти (--show-encoders — то же самое)")
    parser.add_argument("--no-version", action="store_true",
                        help="в списке кодировщиков не запрашивать версии (быстрее, без запуска exe)")
    parser.add_argument("--dry-run", action="store_true",
                        help="показать план задач, ничего не запуская")
    parser.add_argument("-V", "--version", action="version",
                        version=f"{PROG} (encoding_tests) 3.0.0")
    return parser


def _resolve_output_dir(output_dir: Path | None, inputs: list[Path]) -> Path:
    """Определяет выходную папку: явная → ``<первый источник>_Tests``."""
    if output_dir is not None:
        return output_dir
    if not inputs:
        return Path.cwd()
    first = Path(inputs[0])
    if first.is_file():
        return first.parent / f"{first.stem}_Tests"
    return first / "Tests"


def options_from_args(args: argparse.Namespace) -> Options:
    """Собирает :class:`~encoding_tests.pipeline.Options` из разобранных аргументов."""
    opts = Options(
        input_files=list(args.input),
        output_dir=_resolve_output_dir(args.output_dir, list(args.input)),
        encoders=list(args.encoders or DEFAULT_ENCODERS),
        auto_crop=args.auto_crop,
        crop_params=args.crop,
        parallel=args.parallel,
        min_file_size_mb=args.min_file_size,
        lossless_encode=args.lossless_encode,
        no_lossless=args.no_lossless,
        no_encode=args.no_encode,
        force=args.force,
        no_timestamp=args.no_timestamp,
        duration=args.duration,
        template_path=args.template,
        auto_crop_exe=args.autocrop_exe,
        interactive=not args.no_interactive,
        # Флаг --no-preview сохранён для совместимости и больше ни на что не влияет:
        # предпросмотр теперь выключен по умолчанию, включается флагом --preview.
        preview=args.preview,
        log_level=args.log_level,
        show_active_tasks=args.show_active_tasks,
    )
    # Переопределение путей инструментов (если заданы)
    for name in ("vspipe", "ffmpeg", "ffprobe", "ffplay", "mkvmerge", "x265"):
        value = getattr(args, name)
        if value:
            setattr(opts, name, value)
    return opts


def list_encoders(names: list[str] | None = None, *, show_version: bool = True) -> int:
    """Печатает таблицу доступных кодировщиков.

    Args:
        names: имена кодировщиков (пусто или ``None`` — все доступные).
        show_version: запрашивать версию запускаом ``<encoder> --version``.
            Отключается флагом :option:`--no-version`.

    Returns:
        Количество показанных кодировщиков (для кода возврата не используется).
    """
    configs = encoder_configs(names)
    print(ENCODER_TABLE_HEADER)
    print("-" * len(ENCODER_TABLE_HEADER))
    total = 0
    for config in configs.values():
        combinations = get_test_combinations(config)
        total += len(combinations)
        params = ", ".join(f"{key}={list(values)}" for key, values in config.test_params.items())
        version = get_encoder_version(config) if show_version else "-"
        print(f"{config.name:<18} {config.display_name:<24} {len(combinations):>10} "
              f"{version:<16} {config.output_extension:<10} {params}")
    print("-" * len(ENCODER_TABLE_HEADER))
    print(f"Всего: {len(configs)} кодировщиков, {total} комбинаций параметров")
    return len(configs)


_LOG_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "VERBOSE": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _dry_run(pipeline: Pipeline, logger: logging.Logger) -> int:
    """Печатает план работ, ничего не кодируя (``--dry-run``).

    Returns:
        ``0`` — план построен; ``1`` — не найдено ни одного входного файла.
    """
    files = pipeline.collect_input_files()
    if not files:
        logger.error("Не найдено ни одного входного файла")
        return 1

    logger.info("Dry-run: входных файлов %d", len(files))
    for path in files:
        logger.info("  %s", path)

    per_file = 0
    for config in pipeline.configs.values():
        combinations = get_test_combinations(config)
        per_file += len(combinations)
        logger.info("  %-18s %d комбинаций параметров", config.display_name, len(combinations))

    logger.info("Dry-run: %d файл(ов) × %d кодировщик(ов) = %d задач",
                len(files), len(pipeline.configs), per_file * len(files))
    logger.info("Dry-run: кодирование не запускалось")
    return 0


def run(argv: list[str] | None = None) -> int:
    """Основная точка входа CLI. Возвращает код завершения процесса."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_encoders:
        show_versions = not args.no_version
        list_encoders(names=args.encoders if args.encoders else None, show_version=show_versions)
        return 0

    if not args.input:
        parser.error("нужно указать хотя бы один входной файл через --input")

    options = options_from_args(args)
    logger = setup_logging(_LOG_LEVELS.get(options.log_level, logging.INFO))

    pipeline = Pipeline(options)
    if args.dry_run:
        return _dry_run(pipeline, logger)
    return pipeline.run()


def main(argv: list[str] | None = None) -> int:
    """Совместимость с ``python -m encoding_tests.cli``."""
    return run(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
