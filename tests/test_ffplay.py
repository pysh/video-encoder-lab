"""Тесты модуля :mod:`encoding_tests.ffplay` (выбор отрезков и предпросмотр)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from encoding_tests.ffplay import (
    DEFAULT_DURATION,
    MAX_DURATION,
    MIN_DURATION,
    VideoProbeInfo,
    _launch_preview,
    _prompt_float,
    probe_video,
    select_files_with_ffplay,
)

PROBED = VideoProbeInfo(width=1920, height=1080, fps=30.0, duration=120.0)


def _video(tmp_path, name: str) -> str:
    """Создаёт файл-заглушку, чтобы ``Path.is_file()`` внутри функции был True."""
    path = tmp_path / name
    path.write_bytes(b"\x00")
    return str(path)


# --------------------------------------------------------------------------- #
# probe_video
# --------------------------------------------------------------------------- #


class TestProbeVideo:
    def test_missing_file_returns_empty_info(self, tmp_path):
        info = probe_video(str(tmp_path / "absent.mkv"), ffprobe_exe="ffprobe")
        assert info == VideoProbeInfo()

    def test_non_media_file_does_not_raise(self, tmp_path):
        junk = tmp_path / "junk.txt"
        junk.write_text("not a video", encoding="utf-8")
        info = probe_video(str(junk), ffprobe_exe="ffprobe")
        assert isinstance(info, VideoProbeInfo)
        assert info.width == 0


# --------------------------------------------------------------------------- #
# _prompt_float
# --------------------------------------------------------------------------- #


class TestPromptFloat:
    def test_empty_input_returns_default(self):
        with patch("builtins.input", return_value=""):
            assert _prompt_float("Start", 42.0) == 42.0

    def test_eof_returns_default(self):
        with patch("builtins.input", side_effect=EOFError):
            assert _prompt_float("Start", 42.0) == 42.0

    def test_parses_number(self):
        with patch("builtins.input", return_value="10.5"):
            assert _prompt_float("Start", 0.0) == 10.5

    def test_comma_decimal_separator(self):
        with patch("builtins.input", return_value="7,25"):
            assert _prompt_float("Start", 0.0) == 7.25

    def test_retries_on_garbage(self):
        with patch("builtins.input", side_effect=["abc", "5"]):
            assert _prompt_float("Start", 1.0) == 5.0


# --------------------------------------------------------------------------- #
# _launch_preview
# --------------------------------------------------------------------------- #


class TestLaunchPreview:
    def test_skips_when_ffplay_missing(self, tmp_path):
        missing = str(tmp_path / "no_such_ffplay.exe")
        with patch("encoding_tests.ffplay.shutil.which", return_value=None), \
             patch("encoding_tests.ffplay.subprocess.Popen") as m_popen:
            _launch_preview("dummy.mp4", 0.0, missing)
        m_popen.assert_not_called()

    def test_waits_for_process_and_returns(self):
        with patch("encoding_tests.ffplay.shutil.which", return_value="ffplay"), \
             patch("encoding_tests.ffplay.subprocess.Popen") as m_popen:
            _launch_preview("dummy.mp4", 5.0, "ffplay")
        assert m_popen.call_count == 1
        m_popen.return_value.wait.assert_called_once()

    def test_retries_when_launch_fails(self):
        with patch("encoding_tests.ffplay.shutil.which", return_value="ffplay"), \
             patch("encoding_tests.ffplay.subprocess.Popen",
                   side_effect=OSError("no display")) as m_popen:
            _launch_preview("dummy.mp4", 5.0, "ffplay")
        assert m_popen.call_count > 1


# --------------------------------------------------------------------------- #
# select_files_with_ffplay — гейт предпросмотра
# --------------------------------------------------------------------------- #


class TestPreviewGate:
    """``no_preview`` не должен запускать ffplay, но опрос границ сохраняется."""

    @staticmethod
    def _run(tmp_path, *, no_preview=False, auto_accept=False,
             default_duration=DEFAULT_DURATION):
        paths = [_video(tmp_path, "a.mkv"), _video(tmp_path, "b.mkv")]
        prompts = [5.0, default_duration] * 2
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview") as m_preview, \
             patch("encoding_tests.ffplay._prompt_float",
                   side_effect=prompts) as m_prompt:
            results = select_files_with_ffplay(
                paths,
                no_preview=no_preview,
                auto_accept=auto_accept,
                default_duration=default_duration,
            )
        return results, m_preview, m_prompt

    def test_no_preview_suppresses_window(self, tmp_path):
        results, m_preview, m_prompt = self._run(tmp_path, no_preview=True)
        m_preview.assert_not_called()
        assert m_prompt.call_count == 4  # по два запроса на каждый файл
        assert len(results) == 2

    def test_preview_launches_by_default(self, tmp_path):
        _, m_preview, m_prompt = self._run(tmp_path)
        assert m_preview.call_count == 2
        assert m_prompt.call_count == 4

    def test_auto_accept_suppresses_window(self, tmp_path):
        _, m_preview, _ = self._run(tmp_path, auto_accept=True)
        m_preview.assert_not_called()

    def test_no_preview_with_auto_accept(self, tmp_path):
        _, m_preview, _ = self._run(tmp_path, no_preview=True, auto_accept=True)
        m_preview.assert_not_called()

    def test_bounds_identical_with_and_without_preview(self, tmp_path):
        """Единственное отличие флагов — окно; выбранные границы совпадают."""
        with_win, _, _ = self._run(tmp_path)
        no_win, _, _ = self._run(tmp_path, no_preview=True)
        assert [(r.start_frame, r.end_frame, r.frame_count) for r in with_win] == \
               [(r.start_frame, r.end_frame, r.frame_count) for r in no_win]

    def test_default_duration_reaches_prompt(self, tmp_path):
        _, _, m_prompt = self._run(tmp_path, default_duration=45.0)
        # второй вызов на файл — запрос длительности
        assert m_prompt.call_args_list[1].args[1] == 45.0


# --------------------------------------------------------------------------- #
# select_files_with_ffplay — крайние случаи
# --------------------------------------------------------------------------- #


class TestSelectEdgeCases:
    def test_empty_input_returns_empty_list(self):
        assert select_files_with_ffplay([]) == []

    def test_missing_files_are_skipped(self, tmp_path):
        paths = [str(tmp_path / "absent.mkv"), _video(tmp_path, "real.mkv")]
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[1.0, 10.0]):
            results = select_files_with_ffplay(paths, no_preview=True)
        assert len(results) == 1
        assert results[0].original_path.endswith("real.mkv")

    def test_duration_clamped_to_min(self, tmp_path):
        path = _video(tmp_path, "a.mkv")
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[1.0, 0.1]):
            results = select_files_with_ffplay([path], no_preview=True)
        assert results[0].duration_seconds == MIN_DURATION

    def test_duration_clamped_to_max(self, tmp_path):
        path = _video(tmp_path, "a.mkv")
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[1.0, 9999.0]):
            results = select_files_with_ffplay([path], no_preview=True)
        assert results[0].duration_seconds == MAX_DURATION

    def test_frame_math_uses_probed_fps(self, tmp_path):
        path = _video(tmp_path, "a.mkv")
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[10.0, 20.0]):
            results = select_files_with_ffplay([path], no_preview=True)
        assert results[0].start_frame == 300
        assert results[0].end_frame == 900
        assert results[0].frame_count == 600

    def test_crop_params_and_auto_crop_forwarded(self, tmp_path):
        path = _video(tmp_path, "a.mkv")
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[1.0, 10.0]):
            results = select_files_with_ffplay(
                [path], crop_params="1920:1080:0:0", auto_crop=True, no_preview=True)
        assert results[0].crop_params == "1920:1080:0:0"
        assert results[0].auto_crop is True

    def test_result_paths_exist_on_disk(self, tmp_path):
        path = _video(tmp_path, "a.mkv")
        with patch("encoding_tests.ffplay.probe_video", return_value=PROBED), \
             patch("encoding_tests.ffplay._launch_preview"), \
             patch("encoding_tests.ffplay._prompt_float", side_effect=[1.0, 10.0]):
            results = select_files_with_ffplay([path], no_preview=True)
        assert Path(results[0].original_path).is_file()
