# video-encoder-lab

Тестовое кодирование видео разными кодировщиками (SVT-AV1 и др.) с замером
битрейта относительно lossless-эталона. Python-порт скрипта
`Create-TestVideoFiles.ps1`.

Утилита берёт исходные видеофайлы (или папки с ними), вырезает из каждого
тестовый отрезок, готовит для него скрипт фреймсервера VapourSynth, при
необходимости считает кадрирование через `AutoCrop.exe`, кодирует все
комбинации «кодировщик × параметры» и складывает результаты в CSV/Markdown-отчёт
со сравнением битрейтов.

## Возможности

- пакетная обработка файлов и папок (`-i`);
- выбор тестового отрезка: интерактивно, без интерактива или с предпросмотром
  в окне `ffplay`;
- авто-кадрирование (`AutoCrop.exe`) либо ручные параметры `--crop`;
- параллельное кодирование (`-j`);
- список доступных кодировщиков с числом комбинаций параметров и версией
  (`--list-encoders`);
- предварительный просмотр плана без запуска кодирования (`--dry-run`);
- отчёты `test_results*.csv` и `test_results*.md`.

## Требования

- Python 3.11+;
- Windows (пути и утилиты рассчитаны на Windows);
- внешние утилиты: `ffmpeg` / `ffprobe` / `ffplay`, `vspipe` (VapourSynth),
  `mkvmerge` (MKVToolNix), `x265`, `AutoCrop.exe`.

Пути к утилитам можно задать флагами (`--ffmpeg`, `--vspipe`, `--mkvmerge`,
`--x265`, `--ffplay`, `--ffprobe`, `--autocrop-exe`) — значения по умолчанию
рассчитаны на конкретную локальную установку.

## Установка

```powershell
git clone https://github.com/pysh/video-encoder-lab.git
cd video-encoder-lab
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

После установки доступна команда `create-test-video-files`.

Без установки можно запускать из исходного дерева:

```powershell
python run_tests.py --help
python -m encoding_tests --help   # при установленном пакете либо из src
```

## Использование

```powershell
# список кодировщиков с комбинациями и версиями
create-test-video-files --list-encoders

# два файла, lossless-эталон, авто-кроп, SVT-AV1
create-test-video-files -i movie.mkv --lossless-encode --auto-crop --encoders SvtAv1

# папка целиком, без интерактива, 3 параллельные задачи
create-test-video-files -i folder\ --encoders SvtAv1 SvtAv1_VMAF --no-interactive -j 3

# только подготовка (семпл + .vpy + эталон), без кодирования
create-test-video-files -i movie.mkv --lossless-encode --no-encode

# посмотреть план и выйти
create-test-video-files -i movie.mkv --dry-run
```

### Основные параметры

| Параметр | Описание |
|-|-|
| `-i, --input` | входные видеофайлы или папки |
| `-o, --output-dir` | папка результатов (по умолчанию `<источник>_Tests`) |
| `-e, --encoders` | коды кодировщиков |
| `--auto-crop` | авто-кадрирование через `AutoCrop.exe` |
| `--crop L,T,R,B` | ручное кадрирование |
| `-j, --parallel` | число одновременных задач |
| `--duration` | длительность тестового отрезка, сек |
| `--lossless-encode` | создать lossless-эталон (x265) |
| `--no-lossless` | пропустить эталон |
| `--no-encode` | только подготовка, без кодирования |
| `--no-interactive` | не запрашивать границы отрезка |
| `--preview` | показать окно предпросмотра `ffplay` |
| `--force` | пересоздавать семпл |
| `--no-timestamp` | без метки времени в именах результатов |
| `--list-encoders` | показать кодировщики и выйти |
| `--no-version` | не запрашивать версии кодировщиков (быстрее) |
| `--dry-run` | показать план, ничего не запуская |

Предпросмотр `ffplay` по умолчанию выключен; устаревший флаг `--no-preview`
принимается для совместимости и ни на что не влияет.

## Структура проекта

```text
video-encoder-lab/
├── pyproject.toml
├── README.md
├── run_tests.py              # запуск из исходного дерева
├── src/
│   └── encoding_tests/
│       ├── __init__.py
│       ├── __main__.py       # python -m encoding_tests
│       ├── cli.py            # разбор аргументов и точка входа
│       ├── pipeline.py       # конвейер подготовки и кодирования
│       ├── encoders.py       # конфигурации кодировщиков и запуск
│       ├── frame_server.py   # .vpy/.avs скрипты и авто-кроп
│       ├── ffplay.py         # выбор отрезка и предпросмотр
│       ├── ffprobe.py        # метаданные через ffprobe
│       ├── fragments.py      # нарезка фрагментов
│       ├── media_info.py     # HDR/Dolby Vision/bit depth
│       ├── console.py        # цветное логирование
│       ├── translit.py       # транслитерация кириллицы
│       └── tools.py          # пути к внешним утилитам
└── tests/
    ├── test_cli.py
    └── test_ffplay.py
```

Каталоги `Templates/VapourSynth/` (шаблоны `.vpy`) и `Tools/` (`AutoCrop.exe`)
в репозитории не хранятся: пути к ним вычисляются от корня проекта, а при
отсутствии файла используется встроенный шаблон VapourSynth.

## Тесты

```powershell
python -m pytest
```

## Лицензия

MIT
