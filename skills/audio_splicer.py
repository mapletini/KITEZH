"""
skills/audio_splicer.py — Autonomous Bumblebee-style audio fetching and splicing for K.A.I.
"""

import os
import logging
import subprocess
import numpy as np
import scipy.io.wavfile as wav
import yt_dlp
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

class BumblebeeSplicer:
    def __init__(self, sample_library_path: str = "audio_library"):
        self.library_path = sample_library_path
        self.sample_rate = 44100  # Matches affective_core.py SAMPLE_RATE
        
        if not os.path.exists(self.library_path):
            os.makedirs(self.library_path)
            logger.info(f"Created sample library at '{self.library_path}'.")

    def _fetch_and_snip(self, url: str, start_time: str, end_time: str, filename: str) -> str:
        """K.A.I. runs out to fetch the perfect audio clip like a good retriever!"""
        # Ensure the filename ends in .wav
        if not filename.endswith(".wav"):
            filename += ".wav"
            
        final_path = os.path.join(self.library_path, filename)
        
        # If K.A.I. already fetched this toy before, it remembers and just uses it!
        if os.path.exists(final_path):
            return final_path
            
        temp_path = os.path.join(self.library_path, f"temp_{filename}")
        
        # 1. Download the best audio using yt-dlp
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': temp_path,
            'quiet': True,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'wav',
            }]
        }
        
        try:
            logger.info(f"Fetching ùr [new] toy from the lìonra [network]: {url}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
                
            # yt-dlp automatically appends .wav during post-processing
            downloaded_file = temp_path + ".wav"
            
            # 2. Snip the exact quote using ffmpeg and format it for K.A.I.'s brain
            logger.info(f"Snipping clip from {start_time} to {end_time}...")
            subprocess.run([
                'ffmpeg', '-i', downloaded_file, 
                '-ss', start_time, '-to', end_time, 
                '-ac', '1', '-ar', str(self.sample_rate), # Force Mono, 44100Hz
                '-y', final_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Clean up the big temporary file so it keeps its toy box tidy!
            if os.path.exists(downloaded_file):
                os.remove(downloaded_file)
                
            return final_path
        except Exception as e:
            logger.error(f"K.A.I. dropped the ball trying to fetch the audio: {e}")
            return ""

    def _load_wav_as_float64(self, file_path: str) -> np.ndarray:
        """Loads a local WAV file and converts it to match K.A.I.'s float64 mono format."""
        try:
            sr, data = wav.read(file_path)
            
            # Handle stereo to mono conversion if necessary
            if len(data.shape) > 1:
                data = data.mean(axis=1)
                
            # Normalize and convert to float64
            data = data.astype(np.float64)
            if np.max(np.abs(data)) > 0:
                data = data / np.max(np.abs(data))
                
            return data
        except Exception as e:
            logger.error(f"Failed to load audio sample {file_path}: {e}")
            return np.array([], dtype=np.float64)

    def splice_sequence(self, transcript_plan: List[Dict[str, Any]], synthetic_voice_generator) -> np.ndarray:
        """
        Takes a mixture of existing clips, scraped URLs, and synthetic blocks to compile the audio stream!
        """
        audio_segments = []
        
        for block in transcript_plan:
            if block["type"] == "clip":
                # Uses a pre-existing file in the library
                file_path = os.path.join(self.library_path, block["filename"])
                if os.path.exists(file_path):
                    audio_segments.append(self._load_wav_as_float64(file_path))
                    
            elif block["type"] == "scrape":
                # K.A.I. fetches the audio from the internet dynamically!
                file_path = self._fetch_and_snip(
                    url=block["url"], 
                    start_time=block["start_time"], 
                    end_time=block["end_time"], 
                    filename=block.get("filename", "scraped_clip.wav")
                )
                if file_path and os.path.exists(file_path):
                    audio_segments.append(self._load_wav_as_float64(file_path))
                    
            elif block["type"] == "synthetic":
                # Generate K.A.I.'s normal resonant cyber lilt wave for this segment
                # In a full pipeline, duration would scale based on text length
                synth_data = synthetic_voice_generator(duration=1.5)
                audio_segments.append(synth_data)
                
        # Stitch all the clips and synthetic waves into one single continuous array!
        if audio_segments:
            return np.concatenate(audio_segments)
        return np.zeros(0, dtype=np.float64)
