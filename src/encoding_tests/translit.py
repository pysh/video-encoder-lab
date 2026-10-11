"""Транслитерация кириллицы в латиницу.

Порт функции ``ConvertTo-LatinTranslit`` из ``Modules/Utilities.psm1``.
Поддерживаются стандарты ISO 9:1995, Scientific, BGN, Passport, режимы
обработки пути (:paramref:`is_file_path`), удаление диакритики и прочистка имён.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from enum import StrEnum

__all__ = ["TranslitStandard", "to_latin_translit"]

_RUSSIAN_LETTERS = re.compile("[А-Яа-яЁё]")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:$")
_PATH_SPLIT = re.compile(r"[\\/]")

#: Базовая таблица ISO 9:1995.
_ISO_MAP: dict[str, str] = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "Ë", "Ж": "Ž",
    "З": "Z", "И": "I", "Й": "J", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "H", "Ц": "C",
    "Ч": "Č", "Ш": "Š", "Щ": "Ŝ", "Ъ": "ʺ", "Ы": "Y", "Ь": "ʹ", "Э": "È", "Ю": "Û",
    "Я": "Â",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "ë", "ж": "ž",
    "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "č", "ш": "š", "щ": "ŝ", "ъ": "ʺ", "ы": "y", "ь": "ʹ", "э": "è", "ю": "û",
    "я": "â",
}

#: Расширенная таблица (двух-/трёхбуквенные соответствия).
_EXTENDED_MAP: dict[str, str] = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E", "Ж": "Zh",
    "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U", "Ф": "F", "Х": "Kh", "Ц": "Ts",
    "Ч": "Ch", "Ш": "Sh", "Щ": "Sch", "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu",
    "Я": "Ya",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}

#: Правки базовой таблицы для конкретных стандартов.
_STANDARD_OVERRIDES: dict[str, dict[str, str]] = {
    "passport": {
        "Е": "E", "Ё": "E", "Ж": "Zh", "Й": "Y", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
        "Ш": "Sh", "Щ": "Shch", "Ы": "Y", "Ю": "Yu", "Я": "Ya",
        "е": "e", "ё": "e", "ж": "zh", "й": "y", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ы": "y", "ю": "yu", "я": "ya", "ъ": "", "ь": "",
    },
    "bgn": {
        "Е": "Ye", "Ё": "Yo", "Ж": "Zh", "Й": "Y", "Х": "Kh", "Ц": "Ts", "Ч": "Ch",
        "Ш": "Sh", "Щ": "Shch", "Ы": "Y", "Ю": "Yu", "Я": "Ya",
        "е": "ye", "ё": "yo", "ж": "zh", "й": "y", "х": "kh", "ц": "ts", "ч": "ch",
        "ш": "sh", "щ": "shch", "ы": "y", "ю": "yu", "я": "ya", "ъ": "", "ь": "",
    },
    "scientific": {
        "Е": "E", "Ё": "Ë", "Ж": "Ž", "Й": "J", "Х": "H", "Ц": "C", "Ч": "Č",
        "Ш": "Š", "Щ": "Šč", "Ы": "Y", "Ю": "Ju", "Я": "Ja",
        "е": "e", "ё": "ë", "ж": "ž", "й": "j", "х": "h", "ц": "c", "ч": "č",
        "ш": "š", "щ": "šč", "ы": "y", "ю": "ju", "я": "ja",
    },
}


class TranslitStandard(StrEnum):
    """Стандарты транслитерации, поддерживаемые оригинальным скриптом."""

    ISO = "iso"
    SCIENTIFIC = "scientific"
    BGN = "bgn"
    PASSPORT = "passport"


def _build_mapping(standard: TranslitStandard) -> dict[str, str]:
    mapping = dict(_ISO_MAP)
    mapping.update(_STANDARD_OVERRIDES.get(standard.value, {}))
    return mapping


def _clean_name(
    name: str,
    *,
    keep_diacritics: bool = False,
    lowercase: bool = False,
    replace_spaces: bool = False,
    space_replacement: str = "_",
    remove_special_chars: bool = False,
    special_char_replacement: str = "-",
) -> str:
    """Приводит имя в порядок: спецсимволы, пробелы, регистр, диакритика."""
    result = name
    if remove_special_chars:
        result = re.sub(r"[^a-zA-Z0-9\s\-_.]", special_char_replacement, result)
    if replace_spaces:
        result = re.sub(r"\s+", space_replacement, result)
    if lowercase:
        result = result.lower()
    if not keep_diacritics:
        decomposed = unicodedata.normalize("NFD", result)
        result = "".join(char for char in decomposed if ord(char) < 128)
    result = re.sub("_{2,}", "_", result)
    result = re.sub("-{2,}", "-", result)
    return re.sub(r"^[\s_\-]+|[\s_\-]+$", "", result)


def _transliterate(
    text: str,
    mapping: dict[str, str],
    *,
    keep_diacritics: bool = False,
    lowercase: bool = False,
    replace_spaces: bool = False,
    space_replacement: str = "_",
    remove_special_chars: bool = False,
    special_char_replacement: str = "-",
) -> str:
    """Транслитерирует и очищает одну строку (аналог ``Convert-String`` + ``Clean-Name``)."""
    replaced = _RUSSIAN_LETTERS.sub(lambda match: mapping.get(match.group(), match.group()), text)
    return _clean_name(
        replaced,
        keep_diacritics=keep_diacritics,
        lowercase=lowercase,
        replace_spaces=replace_spaces,
        space_replacement=space_replacement,
        remove_special_chars=remove_special_chars,
        special_char_replacement=special_char_replacement,
    )


def _iter_path_parts(path: str) -> Iterator[tuple[bool, str]]:
    """Разбивает путь на части, сохраняя пустые сегменты (для UNC и корня)."""
    for part in _PATH_SPLIT.split(path):
        yield bool(part), part


def to_latin_translit(
    text: str,
    standard: TranslitStandard = TranslitStandard.ISO,
    *,
    keep_diacritics: bool = False,
    use_extended_mapping: bool = False,
    lowercase: bool = False,
    replace_spaces: bool = False,
    space_replacement: str = "_",
    remove_special_chars: bool = False,
    special_char_replacement: str = "-",
    is_file_path: bool = False,
) -> str:
    """Транслитерирует кириллицу в латиницу.

    Args:
        text: исходная строка или путь.
        standard: стандарт транслитерации.
        keep_diacritics: сохранять диакритические знаки после транслитерации.
        use_extended_mapping: использовать расширенную таблицу (``Zh``, ``Sh``...).
        lowercase: привести результат к нижнему регистру.
        replace_spaces: заменить последовательности пробелов.
        space_replacement: чем заменять пробелы.
        remove_special_chars: удалить недопустимые символы.
        special_char_replacement: чем заменять спецсимволы.
        is_file_path: обрабатывать строку как путь (каждый сегмент отдельно,
            буквы дисков и UNC-префикс сохраняются).

    Returns:
        Транслитерированная строка.
    """
    if text is None or (isinstance(text, str) and not text.strip()):
        return text

    mapping = dict(_EXTENDED_MAP) if use_extended_mapping else _build_mapping(standard)
    options = {
        "keep_diacritics": keep_diacritics,
        "lowercase": lowercase,
        "replace_spaces": replace_spaces,
        "space_replacement": space_replacement,
        "remove_special_chars": remove_special_chars,
        "special_char_replacement": special_char_replacement,
    }

    if not is_file_path:
        result = _transliterate(text, mapping, **options)
        return re.sub(r'[<>:"/\\|?*]', "", result)

    is_unc = text.startswith("\\\\")
    parts = [
        part
        if not part or _DRIVE_LETTER.match(part)
        else _transliterate(part, mapping, **options)
        for part in _PATH_SPLIT.split(text)
    ]
    separator = "/" if "/" in text else "\\"
    result = separator.join(parts)
    if is_unc and not result.startswith("\\\\"):
        result = "\\\\" + result.lstrip("\\")
    return result
