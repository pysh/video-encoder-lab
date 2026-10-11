"""Тесты CLI: разбор аргументов и маппинг в :class:`Options`.

Проверяют, что предпросмотр ffplay выключен по умолчанию, включается ключом
``--preview``, а устаревший ``--no-preview`` остаётся принимаемым no-op.
"""

from __future__ import annotations

import argparse

import pytest

from encoding_tests.cli import _crop_params, build_parser, main, options_from_args
from encoding_tests.frame_server import CropParams
from encoding_tests.pipeline import Options


def build_options(argv: list[str]) -> Options:
    """Разбирает аргументы и возвращает ``Options`` без запуска пайплайна."""
    return options_from_args(build_parser().parse_args(["-i", "movie.mkv", *argv]))


# --------------------------------------------------------------------------- #
# предпросмотр
# --------------------------------------------------------------------------- #


def test_preview_off_by_default() -> None:
    assert build_options([]).preview is False


def test_preview_flag_enables_preview() -> None:
    assert build_options(["--preview"]).preview is True


def test_no_preview_still_accepted_and_is_noop() -> None:
    """Устаревший ключ не должен ломать разбор и не включает предпросмотр."""
    assert build_options(["--no-preview"]).preview is False


def test_interactive_is_independent_of_preview() -> None:
    """``--no-interactive`` не должен затрагивать ``preview``."""
    opts = build_options(["--no-interactive"])

    assert opts.interactive is False
    assert opts.preview is False


def test_preview_with_no_interactive() -> None:
    opts = build_options(["--preview", "--no-interactive"])

    assert opts.preview is True
    assert opts.interactive is False


def test_no_preview_hidden_from_help() -> None:
    """Устаревший ключ скрыт из ``--help``, но рабочий."""
    assert "--no-preview" not in build_parser().format_help()


# --------------------------------------------------------------------------- #
# кадрирование
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0,0,0,0", CropParams(0, 0, 0, 0)),
        ("10,20,30,40", CropParams(10, 20, 30, 40)),
        (" 1 , 2 , 3 , 4 ", CropParams(1, 2, 3, 4)),
        ("1;2;3;4", CropParams(1, 2, 3, 4)),
    ],
)
def test_crop_params_parser(value: str, expected: CropParams) -> None:
    assert _crop_params(value) == expected


@pytest.mark.parametrize("value", ["1,2,3", "1,2,3,4,5", "a,b,c,d", "1,-2,3,4", ""])
def test_crop_params_parser_rejects_garbage(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _crop_params(value)


def test_crop_populates_options() -> None:
    opts = build_options(["--crop", "1,2,3,4"])

    assert opts.crop_params == CropParams(1, 2, 3, 4)
    assert opts.auto_crop is False


def test_crop_default_is_zeroed() -> None:
    assert build_options([]).crop_params == CropParams()


def test_crop_garbage_exits_with_code_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["-i", "movie.mkv", "--crop", "bogus"])

    assert excinfo.value.code == 2
