"""Пакет для автоматического тестового кодирования видео и замера метрик качества.

Python-порт PowerShell-скрипта ``Create-TestVideoFiles.ps1`` (v3.0.0).

Модули:
    console       -- цветное консольное логирование (аналог ``Write-Log``);
    translit      -- транслитерация кириллицы в латиницу (аналог ``ConvertTo-LatinTranslit``);
    ffprobe       -- разбор JSON-вывода ffprobe (потоки, версии кодеков);
    media_info    -- сведения о видео/скрипте: HDR, Dolby Vision, bit depth;
    fragments     -- извлечение фрагментов из MKV через mkvmerge;
    frame_server  -- генерация .vpy/.avs скриптов и авто-кроп;
    encoders      -- конфигурации кодировщиков и запуск кодирования;
    ffplay        -- интерактивный просмотр отрезка с OSD таймкода;
    pipeline      -- общий конвейер подготовки файла к тестам;
    cli           -- разбор аргументов и запуск;
    tools         -- пути к внешним утилитам и их проверка.

Расчёт метрик (VMAF/SSIMULACRA2) и CSV-отчёт в текущей версии не входят.
"""

from __future__ import annotations

from .console import setup_logging, write_log
from .encoders import ENCODER_CONFIGS, EncoderConfig, EncoderType
from .frame_server import CropParams
from .tools import Tools, ToolsError

__all__ = [
    "ENCODER_CONFIGS",
    "CropParams",
    "EncoderConfig",
    "EncoderType",
    "Tools",
    "ToolsError",
    "setup_logging",
    "write_log",
]

__version__ = "3.0.0"
