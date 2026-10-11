"""Пути к внешним утилитам и их проверка.

Аналог секции ``Tools Configuration`` из ``Create-TestVideoFiles.ps1``.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, replace
from pathlib import Path

__all__ = ["Tools", "ToolsError"]

MEDIA_SUITE_BIN = Path(r"d:\Sources\media-autobuild_suite\local64\bin-video")


class ToolsError(RuntimeError):
    """Требуемая утилита не найдена."""


@dataclass(frozen=True)
class Tools:
    """Набор внешних исполняемых файлов, необходимых для работы скрипта."""

    ffmpeg: str = str(MEDIA_SUITE_BIN / "ffmpeg.exe")
    ffprobe: str = str(MEDIA_SUITE_BIN / "ffprobe.exe")
    vspipe: str = "vspipe.exe"
    mkvmerge: str = "mkvmerge.exe"
    x265: str = "x265.exe"

    def resolved(self) -> dict[str, str]:
        """Возвращает отображение «имя -> путь» для проверки наличия."""
        return {
            "FFmpeg": self.ffmpeg,
            "FFprobe": self.ffprobe,
            "VSPipe": self.vspipe,
            "MkvMerge": self.mkvmerge,
            "X265": self.x265,
        }

    def with_overrides(
        self,
        *,
        ffmpeg: str | None = None,
        ffprobe: str | None = None,
        vspipe: str | None = None,
        mkvmerge: str | None = None,
        x265: str | None = None,
    ) -> Tools:
        """Возвращает копию с заменой указанных путей (для параметров командной строки)."""
        return replace(self, ffmpeg=ffmpeg or self.ffmpeg, ffprobe=ffprobe or self.ffprobe,
                       vspipe=vspipe or self.vspipe, mkvmerge=mkvmerge or self.mkvmerge,
                       x265=x265 or self.x265)

    def validate(self) -> Tools:
        """Проверяет доступность всех утилит (аналог ``Get-Command``).

        Returns:
            Тот же объект :class:`Tools`, чтобы вызов можно было цеплять.

        Raises:
            ToolsError: если какая-то утилита не найдена.
        """
        missing = [f"{name} = {path}" for name, path in self.resolved().items()
                   if not is_executable_available(path)]
        if missing:
            raise ToolsError("Не найдены требуемые утилиты: " + "; ".join(missing))
        return self


def is_executable_available(command: str) -> bool:
    """Доступен ли исполняемый файл по абсолютному пути или через ``PATH``."""
    if not command:
        return False
    candidate = Path(command)
    if candidate.is_absolute() or candidate.parent != Path(".") or "\\" in command or "/" in command:
        return candidate.is_file()
    return shutil.which(command) is not None
