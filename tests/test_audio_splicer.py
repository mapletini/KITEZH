import tempfile
import unittest
from pathlib import Path

import numpy as np
import scipy.io.wavfile as wav

import main
from skills.audio_splicer import BumblebeeSplicer


class TestAudioSplicer(unittest.TestCase):
    def test_splice_sequence_concatenates_synthetic_and_silence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            splicer = BumblebeeSplicer(sample_library_path=tmpdir)

            def fake_generator(duration: float = 1.0) -> np.ndarray:
                return np.ones(int(duration * 10), dtype=np.float64)

            plan = [
                {"type": "synthetic", "duration": 1.0},
                {"type": "silence", "duration": 0.2},
                {"type": "synthetic", "duration": 0.5},
            ]
            result = splicer.splice_sequence(plan, synthetic_voice_generator=fake_generator)

            self.assertEqual(len(result), 10 + int(0.2 * splicer.sample_rate) + 5)
            self.assertTrue(np.all(result[:10] == 1.0))
            self.assertTrue(np.all(result[-5:] == 1.0))

    def test_splice_sequence_loads_clip_from_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sample_path = Path(tmpdir) / "test_clip.wav"
            sample_rate = 44100
            waveform = np.array([0, 1000, -1000, 500], dtype=np.int16)
            wav.write(sample_path, sample_rate, waveform)
            splicer = BumblebeeSplicer(sample_library_path=tmpdir, sample_rate=sample_rate)

            result = splicer.splice_sequence([{"type": "clip", "filename": "test_clip.wav"}], synthetic_voice_generator=lambda **_: np.zeros(0))

            self.assertEqual(len(result), len(waveform))
            self.assertGreater(np.max(np.abs(result)), 0)


class TestMainAudioSplicerIntegrationHelpers(unittest.TestCase):
    def test_build_synthetic_splice_plan_splits_sentences(self) -> None:
        plan = main.build_synthetic_splice_plan("Hello world. Another sentence!")
        self.assertEqual(plan[0]["type"], "synthetic")
        self.assertEqual(plan[1]["type"], "silence")
        self.assertEqual(plan[2]["type"], "synthetic")

    def test_arg_parser_includes_audio_splicer_flags(self) -> None:
        parser = main.build_arg_parser()
        option_strings = {opt for action in parser._actions for opt in action.option_strings}
        self.assertIn("--audio-splicer", option_strings)
        self.assertIn("--audio-library", option_strings)

    def test_estimate_segment_duration_bounds(self) -> None:
        self.assertEqual(main._estimate_segment_duration(""), 0.6)
        self.assertEqual(main._estimate_segment_duration("word"), 0.6)
        long_text = " ".join(["word"] * 30)
        self.assertEqual(main._estimate_segment_duration(long_text), 3.5)


if __name__ == "__main__":
    unittest.main()
