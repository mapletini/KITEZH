"""
skills/vision.py — Vision, RTSP stream capture, and local AI analysis for K.A.I.
"""

from __future__ import annotations

import os
import cv2
import time
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

class RTSPCameraBridge:
    def __init__(self, workspace_path: str, ollama_url: str = "http://localhost:11434"):
        self.workspace_path = workspace_path
        self.ollama_url = ollama_url
        # Ensure a dedicated local directory exists for visual frames
        self.snapshots_dir = os.path.join(workspace_path, "snapshots")
        os.makedirs(self.snapshots_dir, exist_ok=True)

    def capture_snapshot(self, rtsp_url: str, camera_name: str = "cam_1") -> Optional[str]:
        """
        Connects momentarily to an RTSP stream, grabs the freshest frame, 
        and dumps it as a local JPEG inside the sandboxed workspace.
        """
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;udp"
        
        cap = cv2.VideoCapture(rtsp_url)
        if not cap.isOpened():
            logger.error(f"K.A.I. Vision Error: Cannot open RTSP stream for {camera_name}")
            return None

        try:
            # Drop the first few stale buffered frames to get the absolute live view
            for _ in range(5):
                cap.grab()
                
            ret, frame = cap.read()
            if not ret or frame is None:
                logger.error(f"K.A.I. Vision Error: Failed to retrieve frame from {camera_name}")
                return None

            filename = f"{camera_name}_{int(time.time())}.jpg"
            target_path = os.path.join(self.snapshots_dir, filename)
            
            cv2.imwrite(target_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            logger.info(f"K.A.I. Vision: Successfully stored snapshot to {target_path}")
            return target_path

        finally:
            cap.release()

    def analyze_snapshot(self, image_path: str, prompt: str = "Describe what you see in this image concisely.") -> str:
        """
        Loads a locally saved snapshot, encodes it, and passes it to a local 
        multimodal Ollama model running right on the tower.
        """
        import base64

        if not os.path.exists(image_path):
            return "Error: Snapshot image file does not exist."

        try:
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            # We target a small, fast local vision model like moondream
            payload = {
                "model": "moondream",
                "prompt": prompt,
                "images": [base64_image],
                "stream": False
            }

            response = requests.post(f"{self.ollama_url}/api/generate", json=payload, timeout=30)
            if response.status_code == 200:
                analysis = response.json().get("response", "")
                logger.info(f"K.A.I. Vision: Analysis successful for {image_path}")
                return analysis
            else:
                return f"Error: Local vision model returned status code {response.status_code}"

        except Exception as e:
            logger.error(f"K.A.I. Vision Error: Failed to analyze image: {str(e)}")
            return f"Error during analysis: {str(e)}"
