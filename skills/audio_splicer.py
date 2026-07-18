"""
skills/audio_splicer.py — Audio fetch/trim/compose pipeline used by K.A.I.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np
from scipy import signal
import scipy.io.wavfile as wav
import yt_dlp

logger = logging.getLogger(__name__)

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class BumblebeeSplicer:
    def __init__(self, sample_library_path: str = "audio_library", sample_rate: int = 44100) -> None:
        self.library_path = Path(sample_library_path)
        self.sample_rate = int(sample_rate)
        self.library_path.mkdir(parents=True, exist_ok=True)

    def _normalize_filename(self, filename: str, default_stem: str) -> str:
        stem = (filename or default_stem).strip()
        safe_stem = _SAFE_FILENAME_RE.sub("_", stem).strip("._")
        if not safe_stem:
            safe_stem = default_stem
        if not safe_stem.endswith(".wav"):
            safe_stem = f"{safe_stem}.wav"
        return safe_stem

    def _fetch_and_snip(self, url: str, start_time: str, end_time: str, filename: str) -> str:
        """Fetches remote audio and trims it into a normalized local WAV file.

        Returns an empty string when fetch/trim fails so callers can skip that block.
        """
        if shutil.which("ffmpeg") is None:
            logger.error("ffmpeg not found in PATH; install ffmpeg to enable scraped audio trimming.")
            return ""

        output_name = self._normalize_filename(filename, "scraped_clip")
        final_path = self.library_path / output_name
        if final_path.exists():
            return str(final_path)

        with tempfile.TemporaryDirectory(prefix="bumblebee_splicer_") as temp_dir:
            temp_base = Path(temp_dir) / "downloaded_input"
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(temp_base),
                "quiet": True,
                "noplaylist": True,
            }
            try:
                logger.info("Fetching audio source for clip splice: %s", url)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    downloaded_file = Path(ydl.prepare_filename(info))

                subprocess.run(
                    [
                        "ffmpeg",
                        "-i",
                        str(downloaded_file),
                        "-ss",
                        start_time,
                        "-to",
                        end_time,
                        "-ac",
                        "1",
                        "-ar",
                        str(self.sample_rate),
                        "-y",
                        str(final_path),
                    ],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return str(final_path)
            except Exception as exc:
                logger.error("Failed to fetch/trim scraped clip (%s): %s", url, exc)
                return ""

    def _load_wav_as_float64(self, file_path: str | Path) -> np.ndarray:
        """Loads a local WAV file and converts it to normalized float64 mono."""
        try:
            sample_rate, data = wav.read(str(file_path))
            if data.ndim > 1:
                data = data.mean(axis=1)
            data = data.astype(np.float64)
            peak = np.max(np.abs(data)) if data.size > 0 else 0.0
            if peak > 0:
                data = data / peak
            if sample_rate != self.sample_rate and data.size > 0:
                target_len = max(1, int((len(data) * self.sample_rate) / sample_rate))
                data = signal.resample(data, target_len)
            return data
        except Exception as exc:
            logger.error("Failed to load audio sample %s: %s", file_path, exc)
            return np.zeros(0, dtype=np.float64)

    def splice_sequence(
        self,
        transcript_plan: list[dict[str, Any]],
        synthetic_voice_generator: Callable[..., np.ndarray],
    ) -> np.ndarray:
        """
        Composes a timeline from blocks:
        - clip: {"type":"clip","filename":"foo.wav"}
        - scrape: {"type":"scrape","url":"...","start_time":"00:00:01","end_time":"00:00:02","filename":"name.wav"}
        - synthetic: {"type":"synthetic","duration":1.2}
        - silence: {"type":"silence","duration":0.25}
        """
        audio_segments: list[np.ndarray] = []
        for block in transcript_plan:
            block_type = str(block.get("type", "")).strip().lower()
            if block_type == "clip":
                filename = block.get("filename")
                if not filename:
                    continue
                file_path = self.library_path / self._normalize_filename(str(filename), "clip")
                if file_path.exists():
                    segment = self._load_wav_as_float64(file_path)
                    if segment.size > 0:
                        audio_segments.append(segment)
            elif block_type == "scrape":
                file_path = self._fetch_and_snip(
                    url=str(block.get("url", "")),
                    start_time=str(block.get("start_time", "00:00:00")),
                    end_time=str(block.get("end_time", "00:00:01")),
                    filename=str(block.get("filename", "scraped_clip.wav")),
                )
                if file_path:
                    segment = self._load_wav_as_float64(file_path)
                    if segment.size > 0:
                        audio_segments.append(segment)
            elif block_type == "synthetic":
                duration = float(block.get("duration", 1.5))
                if duration <= 0:
                    continue
                synth_data = synthetic_voice_generator(duration=duration)
                segment = np.asarray(synth_data, dtype=np.float64)
                if segment.size > 0:
                    audio_segments.append(segment)
            elif block_type == "silence":
                duration = max(0.0, float(block.get("duration", 0.0)))
                if duration > 0:
                    samples = int(duration * self.sample_rate)
                    audio_segments.append(np.zeros(samples, dtype=np.float64))

        if audio_segments:
            return np.concatenate(audio_segments)
        return np.zeros(0, dtype=np.float64)
