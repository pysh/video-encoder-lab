"""Генерация frameserver-скриптов и авто-кроп.

Порт функций ``New-FrameServerScript`` и ``Get-VideoAutoCropParams``
из ``Create-TestVideoFiles.ps1``.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("encoding_tests")

__all__ = ["CropParams", "create_frame_server_script", "get_auto_crop_params"]

# Шаблоны и внешние утилиты лежат в корне репозитория (на уровень выше ``src``).
_REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TEMPLATE = _REPO_ROOT / "Templates" / "VapourSynth" / "universal_v2_DoVi.vpy"
DEFAULT_TEMPLATE_LEGACY = Path(r"d:\PSScripts\Convert-VideoToAV1\Templates\VapourSynth\universal_v2_DoVi.vpy")
AUTO_CROP_DEFAULT = _REPO_ROOT / "Tools" / "AutoCrop.exe"
AUTO_CROP_LEGACY = Path(r"d:\PSScripts\Convert-VideoToAV1\Tools\AutoCrop.exe")

AVISYNTH_AUTOLOAD = "X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\FrameServer\\AviSynth\\plugins\\"
AVISYNTH_LSMASH = "X:\\Apps\\_VideoEncoding\\StaxRip\\Apps\\Plugins\\Dual\\L-Smash-Works\\LSMASHSource.dll"

# --- встроенный шаблон VapourSynth (fallback) ---
# Плейсхолдеры совпадают с Templates/VapourSynth/universal_v2_DoVi.vpy: {VideoPath}, ...
_BUILTIN_TEMPLATE = '''\
import vapoursynth as vs
core = vs.core
clip = core.lsmas.LWLibavSource(source=r"{VideoPath}", cachefile=r"{CacheFile}", prefer_hw=4, threads=1)
clip = core.std.Crop(clip, {CropLeft}, {CropRight}, {CropTop}, {CropBottom})
clip = core.fmtc.bitdepth(clip, bits=10)
clip.set_output()
'''


@dataclass(frozen=True)
class CropParams:
    """Параметры кадрирования (лево, верх, право, низ)."""

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def is_empty(self) -> bool:
        """``True``, если кадрирование нулевое."""
        return not (self.left or self.top or self.right or self.bottom)


def _resolve_template(template_path: Path | None, source_video: Path) -> Path:
    """Возвращает путь к шаблону ``.vpy``.

    Приоритет: явный путь → файл рядом с исходным видео → встроенный репозиторий → legacy.
    """
    if template_path and template_path.is_file():
        return template_path
    local = source_video.parent / "template.vpy"
    if local.is_file():
        return local
    if DEFAULT_TEMPLATE.is_file():
        return DEFAULT_TEMPLATE
    return DEFAULT_TEMPLATE_LEGACY


def _read_template(template_path: Path) -> str:
    """Читает шаблон ``.vpy``.

    Если файл не найден, возвращает встроенный шаблон.
    """
    if template_path.is_file():
        return template_path.read_text(encoding="utf-8")
    return _BUILTIN_TEMPLATE


def create_frame_server_script(
    script_path: str | Path,
    video_path: str | Path,
    crop: CropParams,
    source_video_path: str | Path | None = None,
    template_path: str | Path | None = None,
) -> tuple[Path, Path]:
    """Создаёт .vpy и .avs скрипты для фреймсервера.

    Args:
        script_path: базовое имя выходных скриптов.
        video_path: путь к видео (семплу или исходному файлу).
        crop: параметры кадрирования.
        source_video_path: путь к исходному файлу (для поиска ``template.vpy`` рядом).
        template_path: путь к шаблону ``.vpy``.

    Returns:
        Кортеж ``(vpy_path, avs_path)``.
    """
    base = Path(script_path)
    base.parent.mkdir(parents=True, exist_ok=True)
    video = Path(video_path)
    cache_file = str(video) + ".lwi"
    source = Path(source_video_path) if source_video_path else video
    tpl = _resolve_template(template_path, source)
    tpl_content = _read_template(tpl)
    vpy_path = base.with_suffix(".vpy")
    avs_path = base.with_suffix(".avs")

    # Генерация VapourSynth из шаблона
    vpy_content = tpl_content
    placeholders = {
        "{VideoPath}": str(video),
        "{CacheFile}": cache_file,
        "{CropLeft}": str(crop.left),
        "{CropRight}": str(crop.right),
        "{CropTop}": str(crop.top),
        "{CropBottom}": str(crop.bottom),
    }
    for old, new in placeholders.items():
        vpy_content = vpy_content.replace(old, new)

    vpy_path.write_text(vpy_content, encoding="utf-8")

    # Генерация AviSynth
    avs_content = f"""\
AddAutoloadDir("{AVISYNTH_AUTOLOAD}")
LoadPlugin("{AVISYNTH_LSMASH}")
LWLibavVideoSource("{video}")
Crop({crop.left}, {crop.top}, -{crop.right}, -{crop.bottom})
"""
    avs_path.write_text(avs_content, encoding="utf-8")
    return vpy_path, avs_path


def get_auto_crop_params(
    input_file: str | Path,
    luminance_threshold: int = 1000,
    round_to: int = 2,
    auto_crop_exe: str | Path | None = None,
) -> CropParams:
    """Определяет параметры авто-кадрирования через ``AutoCrop.exe``.

    Запускает временный ``.vpy`` скрипт (конвертация в RGB24 через libp2p.Pack),
    передаёт его в AutoCrop и парсит строку результата.

    Args:
        input_file: исходное видео.
        luminance_threshold: порог яркости.
        round_to: округлять кроп до кратного значения.
        auto_crop_exe: путь к ``AutoCrop.exe``.

    Returns:
        :class:`CropParams` с четырьмя сторонами.
    """
    source = Path(input_file)
    if not source.is_file():
        raise FileNotFoundError(f"Файл не найден: {source}")
    cache_file = str(source) + ".lwi"

    # Временный .vpy: конвертация в RGB24 для AutoCrop
    tmp_script = source.with_name(f"{source.stem}_autocrop.vpy")
    try:
        vpy_content = f"""\
import vapoursynth as vs
core = vs.core
clip = core.lsmas.LWLibavSource(source=r"{source}", cachefile=r"{cache_file}", prefer_hw=4, threads=1)
props = clip.get_frame(0).props

MATRIX = {{'RGB': 0, 'BT709': 1, 'UNSPEC': 2, 'BT470BG': 5, 'BT2020_NCL': 9}}
TRANSFER = {{'BT709': 1, 'BT470BG': 5, 'ST2084': 16}}
PRIMARIES = {{'BT709': 1, 'BT470BG': 5, 'BT2020': 9}}

matrix = props.get('_Matrix', MATRIX['UNSPEC'])
if matrix == MATRIX['UNSPEC'] or matrix >= 15:
    matrix = MATRIX['RGB'] if clip.format.id == vs.RGB24 else MATRIX['BT709']

transfer = props.get('_Transfer', TRANSFER['BT709'])
if transfer <= 0 or transfer >= 19:
    transfer = TRANSFER['BT470BG'] if matrix == MATRIX['BT470BG'] else TRANSFER['BT709']

primaries = props.get('_Primaries', PRIMARIES['BT709'])
if primaries <= 0 or primaries >= 23:
    primaries = PRIMARIES['BT470BG'] if matrix == MATRIX['BT470BG'] else PRIMARIES['BT709']

clip = clip.resize.Bicubic(matrix_in=matrix, transfer_in=transfer, primaries_in=primaries, format=vs.RGB24)
clip = clip.libp2p.Pack()
clip.set_output()
"""
        tmp_script.write_text(vpy_content, encoding="utf-8")

        exe = Path(auto_crop_exe) if auto_crop_exe else AUTO_CROP_DEFAULT
        if not exe.is_file():
            exe = AUTO_CROP_LEGACY
        if not exe.is_file():
            log.error("AutoCrop.exe не найден: %s", auto_crop_exe or AUTO_CROP_DEFAULT)
            return CropParams()

        args = [str(exe), str(tmp_script), "2", "400", "144", "144", str(luminance_threshold), "0"]
        result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", check=False)
        if result.returncode != 0:
            details = (result.stderr or result.stdout or "").strip()
            log.error("AutoCrop завершился с кодом %d: %s", result.returncode, details)
            return CropParams()

        # Парсим последнюю непустую строку вывода
        output = (result.stdout or "") + "\n" + (result.stderr or "")
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            log.warning("AutoCrop не вернул данных, кадрирование не применяется")
            return CropParams()
        try:
            parts = [int(part.strip()) for part in lines[-1].split(",")]
        except ValueError:
            log.warning("Не удалось разобрать вывод AutoCrop: %s", lines[-1])
            return CropParams()
        if len(parts) != 4:
            log.warning("Ожидалось 4 значения кадрирования, получено %d: %s",
                        len(parts), lines[-1])
            return CropParams()
        return CropParams(
            left=_round_multiple(parts[0], round_to),
            top=_round_multiple(parts[1], round_to),
            right=_round_multiple(parts[2], round_to),
            bottom=_round_multiple(parts[3], round_to),
        )
    except OSError as exc:
        log.error("Ошибка запуска AutoCrop: %s", exc)
    finally:
        tmp_script.unlink(missing_ok=True)
    return CropParams()


def _round_multiple(value: int, multiple: int) -> int:
    """Округляет ``value`` до ближайшего кратного ``multiple`` (как ``Math::Round``.NET)."""
    if multiple == 0:
        return value
    return round(value / multiple) * multiple
