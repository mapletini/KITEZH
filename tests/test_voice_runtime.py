"""Tests for skills/voice_runtime.py."""

import unittest

from skills.voice_runtime import DecoderConfig, HybridSpeechRuntime, RollingAudioBuffer


class TestRollingAudioBuffer(unittest.TestCase):
    def test_buffer_rolls_over_capacity(self) -> None:
        buf = RollingAudioBuffer(max_seconds=1, sample_rate=10)
        buf.push([float(i) for i in range(15)])
        snap = buf.snapshot()
        self.assertEqual(len(snap), 10)
        self.assertEqual(snap[0], 5.0)


class TestHybridSpeechRuntime(unittest.TestCase):
    def _make_runtime(self) -> HybridSpeechRuntime:
        cfg = DecoderConfig(
            sample_rate=16_000,
            frame_ms=20,
            decode_interval_ms=40,
            endpoint_silence_ms=60,
            bargein_confidence=0.65,
            partial_stability_required=2,
            buffer_seconds=30,
        )

        def partial_decoder(segment):
            return ("hello", 0.8) if segment else ("", 0.0)

        def final_decoder(segment):
            return ("hello world", 0.9) if segment else ("", 0.0)

        return HybridSpeechRuntime(config=cfg, partial_decoder=partial_decoder, final_decoder=final_decoder)

    def test_emits_barge_in_when_tts_active_and_confident(self) -> None:
        rt = self._make_runtime()
        events = rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.8, tts_active=True)
        self.assertTrue(any(e.kind == "barge_in" for e in events))

    def test_emits_partial_and_stable_partial(self) -> None:
        rt = self._make_runtime()
        e1 = rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.9, tts_active=False)
        e2 = rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.9, tts_active=False)
        e3 = rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.9, tts_active=False)
        e4 = rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.9, tts_active=False)
        kinds = [e.kind for e in (e1 + e2 + e3 + e4)]
        self.assertIn("partial", kinds)
        self.assertIn("partial_stable", kinds)

    def test_emits_final_on_silence_endpoint(self) -> None:
        rt = self._make_runtime()
        rt.ingest_frame(frame=[0.1] * 320, speech_confidence=0.9, tts_active=False)
        e2 = rt.ingest_frame(frame=[0.0] * 320, speech_confidence=0.1, tts_active=False)
        e3 = rt.ingest_frame(frame=[0.0] * 320, speech_confidence=0.1, tts_active=False)
        e4 = rt.ingest_frame(frame=[0.0] * 320, speech_confidence=0.1, tts_active=False)
        kinds = [e.kind for e in (e2 + e3 + e4)]
        self.assertIn("final", kinds)


if __name__ == "__main__":
    unittest.main()
