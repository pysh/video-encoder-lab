"""Конфигурации кодировщиков и запуск кодирования.

Порт секций ``Encoder Configurations``, ``Get-TestCombinations``,
``Get-EncoderVersion``, ``Invoke-EncoderProcess`` и ``Get-LosslessSample``
из ``Create-TestVideoFiles.ps1``.
"""

from __future__ import annotations

import itertools
import logging
import re
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

log = logging.getLogger("encoding_tests")

__all__ = [
    "DEFAULT_ENCODERS",
    "ENCODER_CONFIGS",
    "EncoderConfig",
    "EncoderType",
    "encode_one",
    "encoder_configs",
    "get_encoder_configs",
    "get_encoder_version",
    "get_output_file_name",
    "get_parameters_string",
    "get_test_combinations",
    "lossless_encode",
]

# --- Типы кодировщиков ---

class EncoderType(StrEnum):
    SVT_AV1 = "svt"
    X26X = "x26x"
    AOM = "aom"


# --- Конфигурация кодировщика ---

@dataclass(frozen=True)
class EncoderConfig:
    """Настройки одного кодировщика."""

    name: str
    display_name: str
    executable: str
    type: EncoderType
    common_params: tuple[str, ...]
    test_params: dict[str, tuple[object, ...]]
    output_extension: str


# --- Карта кодировщиков (аналог $script:EncoderConfigs) ---

# Общие параметры для SVT-AV1 (из оригинала, ``--lp`` закомментирован)
_SVT_AV1_COMMON = (
    "--progress", "2",
    "--rc", "0",
    "--enable-qm", "1",
    "--film-grain-denoise", "0",
    "--film-grain", "12",
    "--color-range", "0",
    "--color-primaries", "1",
    "--transfer-characteristics", "1",
    "--matrix-coefficients", "1",
)

_SVT_AV1_ESSENTIAL_COMMON = (
    "--progress", "2",
    "--rc", "0",
    "--enable-qm", "1",
    # '--tune', '2',
    "--film-grain-denoise", "0",
    "--film-grain", "10",
    "--color-range", "0",
    "--color-primaries", "1",
    "--transfer-characteristics", "1",
    "--matrix-coefficients", "1",
)

_SVT_AV1_PSY_COMMON = (
    "--progress", "3",
    "--rc", "0",
    "--film-grain-denoise", "0",
    "--film-grain", "10",
    "--color-primaries", "1",
    "--transfer-characteristics", "1",
    "--matrix-coefficients", "1",
)

_ENCODER_CONFIGS_RAW: dict[str, EncoderConfig] = {
    "SvtAv1": EncoderConfig(
        name="SvtAv1",
        display_name="SVT-AV1",
        executable="X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Encoders\\SvtAv1EncApp\\SvtAv1EncApp.exe",
        type=EncoderType.SVT_AV1,
        common_params=_SVT_AV1_COMMON,
        test_params={
            "crf": (30, 32, 34, 36, 38),
            "preset": (5,),
            "tune": (1, 4),
        },
        output_extension="ivf",
    ),
    "SvtAv1_VMAF": EncoderConfig(
        name="SvtAv1_vmaf",
        display_name="SVT-AV1_vmaf",
        executable="X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Encoders\\SvtAv1EncApp\\SvtAv1EncApp.exe",
        type=EncoderType.SVT_AV1,
        common_params=_SVT_AV1_COMMON,
        test_params={
            "crf": (34, 36, 38),
            "preset": (5,),
            "tune": (5,),
        },
        output_extension="ivf",
    ),
    "SvtAv1EncTritium": EncoderConfig(
        name="SvtAv1_tritium",
        display_name="SvtAv1Enc Tritium",
        executable="X:\\Apps\\_VideoEncoding\\ffmpeg\\svt-av1-tritium\\SvtAv1EncApp_icelake.exe",
        type=EncoderType.SVT_AV1,
        common_params=_SVT_AV1_COMMON,
        test_params={
            "crf": (40, 42, 44),
            "preset": (5,),
            "tune": (1, 4, 5),
            # tune: 0=VQ, 1=PSNR, 2=SSIM, 3=IQ, 4=MS_SSIM, 5=Film Grain
        },
        output_extension="ivf",
    ),
    "SvtAv1Essential": EncoderConfig(
        name="SvtAv1Essential",
        display_name="SVT-AV1 Essential",
        executable="X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Encoders\\SvtAv1EncApp-Essential\\SvtAv1EncApp.exe",
        type=EncoderType.SVT_AV1,
        common_params=_SVT_AV1_ESSENTIAL_COMMON,
        test_params={
            "quality": ("high", "medium", "low", "lower"),
            "speed": ("slow",),
        },
        output_extension="webm",
    ),
    "SvtAv1PSY": EncoderConfig(
        name="SvtAv1PSY",
        display_name="SVT-AV1 (Psychovisual)",
        executable="X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Encoders\\SvtAv1EncApp-PSYEX\\SvtAv1EncApp.exe",
        type=EncoderType.SVT_AV1,
        common_params=_SVT_AV1_PSY_COMMON,
        test_params={
            "crf": (38, 36, 34),
            "preset": (4,),
        },
        output_extension="ivf",
    ),
    "x265": EncoderConfig(
        name="x265",
        display_name="x265 (HEVC)",
        executable="X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Encoders\\x265\\x265.exe",
        type=EncoderType.X26X,
        common_params=(
            "--output-depth", "10",
            "--range", "limited",
            "--colorprim", "bt709",
            "--transfer", "bt709",
            "--colormatrix", "bt709",
        ),
        test_params={
            "crf": (18, 20, 22, 24, 26, 28),
            "preset": ("slower",),
        },
        output_extension="hevc",
    ),
    "x264": EncoderConfig(
        name="x264",
        display_name="x264 (AVC)",
        executable="X:\\Apps\\_VideoEncoding\\av1an\\x264.exe",
        type=EncoderType.X26X,
        common_params=(
            "--no-progress",
            "--demuxer", "y4m",
            "--output-csp", "i420",
            "--tune", "ssim",
        ),
        test_params={
            "crf": (18, 20, 22, 24, 26, 28, 30),
            "preset": ("medium", "slow", "slower", "veryslow"),
        },
        output_extension="h264",
    ),
}

#: Публичная карта конфигураций кодировщиков (аналог ``$script:EncoderConfigs``).
ENCODER_CONFIGS: dict[str, EncoderConfig] = _ENCODER_CONFIGS_RAW

#: Кодировщики, используемые, если они не заданы явно (аналог значения по умолчанию
#: ``$EncoderConfigs`` в оригинальном скрипте).
DEFAULT_ENCODERS: list[str] = ["SvtAv1", "SvtAv1_VMAF", "SvtAv1EncTritium"]


def encoder_configs(names: list[str] | None = None) -> dict[str, EncoderConfig]:
    """Возвращает конфигурации кодировщиков по именам.

    Аналог выбора кодировщиков из оригинального скрипта: имена регистрозависимы,
    неизвестные имена пропускаются с предупреждением.

    Args:
        names: список требуемых имен. Если пуст или ``None`` — возвращаются все.

    Returns:
        Словарь ``{имя: конфигурация}`` в порядке запроса без дубликатов.
    """
    if not names:
        return dict(ENCODER_CONFIGS)
    selected: dict[str, EncoderConfig] = {}
    for name in names:
        if name in ENCODER_CONFIGS:
            selected[name] = ENCODER_CONFIGS[name]
        else:
            log.warning("Неизвестный кодировщик: %s", name)
    return selected


#: Устаревшее имя :func:`encoder_configs` (для обратной совместимости).
get_encoder_configs = encoder_configs


def get_test_combinations(config: EncoderConfig) -> list[dict[str, object]]:
    """Картезианское произведение параметров тестирования.

    Порядок ключей в каждой комбинации сохраняется по алфавиту, как в оригинале
    ``$paramNames = $EncoderConfig.TestParams.Keys | Sort-Object``.
    """
    sorted_keys = sorted(config.test_params)
    values = [config.test_params[key] for key in sorted_keys]
    return [dict(zip(sorted_keys, combo, strict=True)) for combo in itertools.product(*values)]


def get_encoder_version(config: EncoderConfig) -> str:
    """Извлекает версию кодировщика из вывода ``--version``."""
    try:
        result = subprocess.run(
            [config.executable, "--version"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
        if result.returncode != 0 and not result.stdout.strip():
            return "Unknown"
        version_line = (result.stdout + "\n" + result.stderr).splitlines()[0]
        if config.name.startswith("SvtAv1"):
            match = re.search(r"SVT-AV1(?:-PSY|-HDR|-Essential|-Tritium)?\s(v[\d.]+(?:-[^-]+)?)", version_line)
            if match:
                return match.group(1)
        elif config.name == "x265":
            match = re.search(r"HEVC encoder version\s*(.+)$", version_line)
            if match:
                return match.group(1).strip()
        elif config.name == "x264":
            match = re.search(r"x264\s+([\d.]+)", version_line)
            if match:
                return match.group(1)
    except FileNotFoundError:
        pass
    return "Unknown"


def get_parameters_string(test_params: dict[str, object]) -> str:
    """Формирует строку параметров ``key=value`` (отсортировано по ключу)."""
    return " ".join(f"{key}={test_params[key]}" for key in sorted(test_params))


def get_output_file_name(
    config: EncoderConfig,
    test_params: dict[str, object],
    file_prefix: str = "",
    file_suffix: str = "",
) -> str:
    """Генерирует имя файла результата кодирования.

    Формат: ``{FilePrefix}{Name}_{key=val}_{FileSuffix}.mkv``
    """
    param_str = get_parameters_string(test_params)
    if param_str:
        param_str = f"_{param_str}"
    return f"{file_prefix}{config.name}{param_str}{file_suffix}.mkv"


def _build_encoder_params(config: EncoderConfig, test_params: dict[str, object]) -> list[str]:
    """Собирает аргументы командной строки из ``test_params``."""
    parts: list[str] = []
    for key in sorted(test_params):
        value = test_params[key]
        # aom-стиль: --key=value (в оригинале не используется, но сохранён)
        if config.type == EncoderType.AOM:
            parts.append(f"--{key}={value}")
        else:
            parts.append(f"--{key}")
            parts.append(str(value))
    return parts


def _run_pipeline(producer: list[str], consumer: list[str]) -> tuple[int, int]:
    """Запускает конвейер ``producer | consumer``.

    Возвращает ``(rc_producer, rc_consumer)``. Код возврата ``producer`` может быть
    ненулевым, если приёмщик закрыл поток раньше (например, по ``--frames``),
    поэтому ошибка определяется по ``rc_consumer``.

    Выходные потоки конвейера наследуются (как в PowerShell),
    чтобы прогресс-информация кодировщика была видна в консоли.
    """
    with (
        subprocess.Popen(producer, stdout=subprocess.PIPE) as src,
        subprocess.Popen(consumer, stdin=src.stdout) as dst,
    ):
        src.stdout.close()
        dst.wait()
        return src.wait(), dst.returncode


def encode_one(
    config: EncoderConfig,
    test_params: dict[str, object],
    input_script: str | Path,
    output_file: str | Path,
    server_type: str = "VapourSynth",
    frame_count: int = 0,
    vspipe: str = "vspipe.exe",
    mkvmerge: str = "mkvmerge.exe",
    ffmpeg: str = "ffmpeg.exe",
) -> Path:
    """Кодирует один тестовый файл.

    Аналог ``Invoke-EncoderProcess`` из оригинала.

    Args:
        config: конфигурация кодировщика.
        test_params: параметры теста (crf, preset, tune и т.д.).
        input_script: путь к .vpy или .avs скрипту.
        output_file: целевой .mkv файл.
        server_type: тип фреймсервера (``VapourSynth`` / ``AviSynth``).
        frame_count: число кадров (0 = все).
        vspipe: путь к ``vspipe.exe``.
        mkvmerge: путь к ``mkvmerge.exe``.
        ffmpeg: путь к ``ffmpeg.exe`` (нужен для AviSynth-конвейера).

    Returns:
        Путь к созданному MKV-файлу.

    Raises:
        RuntimeError: если кодирование завершилось ошибкой.
    """
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.parent / f"temp_{output_path.stem}.{config.output_extension}"

    cmd_params = _build_encoder_params(config, test_params)
    all_params = list(config.common_params) + cmd_params

    # --- x26x (x264 / x265) ---
    if config.type == EncoderType.X26X:
        producer = [vspipe, "-c", "y4m", str(input_script), "-"]
        if config.name == "x264":
            # x264 читает y4m из stdin как позиционный аргумент "-"
            consumer = [config.executable, *all_params, "-o", str(temp_output), "-"]
        else:
            consumer = [config.executable, *all_params,
                        "--y4m", "--input", "-", "--output", str(temp_output)]
        _, encoder_rc = _run_pipeline(producer, consumer)
    else:
        # --- SVT-AV1 / AOM ---
        av1_args = ["--frames", str(frame_count), "--input", "-", "--output", str(temp_output)]
        if server_type == "VapourSynth":
            producer = [vspipe, "-c", "y4m", str(input_script), "-"]
        else:
            # AviSynth: ffmpeg читает .avs и отдаёт yuv4mpegpipe в кодировщик
            producer = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-i",
                        str(input_script), "-f", "yuv4mpegpipe", "-strict", "-1", "-"]
        consumer = [config.executable, *all_params, *av1_args]
        _, encoder_rc = _run_pipeline(producer, consumer)

    if encoder_rc != 0:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(f"{config.executable} завершился с кодом {encoder_rc}")

    # Ожидание появления файла (некоторые сборки закрывают его с задержкой)
    for _ in range(5):
        if temp_output.exists():
            break
        time.sleep(0.5)

    if not temp_output.exists():
        raise RuntimeError(f"Файл не создан: {temp_output}")

    file_size = temp_output.stat().st_size
    if file_size == 0:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(f"Файл пустой: {temp_output}")

    # Упаковка в MKV
    result = subprocess.run(
        [mkvmerge, "--ui-language", "en", "--priority", "lower",
         "--output", str(output_path), str(temp_output)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"mkvmerge завершился с кодом {result.returncode}: {details}")

    temp_output.unlink(missing_ok=True)
    return output_path


def lossless_encode(
    input_script: str | Path,
    output_file: str | Path,
    frame_count: int,
    x265_exe: str = "x265.exe",
) -> Path:
    """Кодирует без потерь через x265.

    Аналог ``Get-LosslessSample`` из оригинала.

    Args:
        input_script: путь к .vpy скрипту (источник).
        output_file: путь к итоговому файлу (расширение .x265 добавится автоматически).
        frame_count: число кадров.
        x265_exe: путь к исполняемому файлу x265.

    Returns:
        Путь к созданному файлу.

    Raises:
        RuntimeError: если файл не создан.
    """
    output_path = Path(output_file)
    x265_path = output_path.with_suffix(".x265")

    args = [x265_exe]
    if frame_count > 0:
        args += ["--frames", str(frame_count)]
    args += [
        "--reader-options", "library=C:\\Python313\\Lib\\site-packages\\vapoursynth\\vsscript.dll",
        "--output-depth", "10",
        "--video-signal-type-preset", "BT709_YCC",
        "--lossless",
        "--input", str(input_script),
        "--output", str(x265_path),
    ]

    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", check=False)
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"x265 завершился с кодом {result.returncode}: {details}")

    if not x265_path.exists():
        raise RuntimeError(f"x265 не создал файл: {x265_path}")
    return x265_path
