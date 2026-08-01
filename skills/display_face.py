from __future__ import annotations

import argparse
import logging
import math
import os
import time

import config
from skills.display_bridge import load_display_state

logger = logging.getLogger(__name__)

try:
    import pygame
except ImportError:  # pragma: no cover - optional dependency
    pygame = None
    logger.info("pygame is unavailable; framebuffer face rendering will be disabled.")


def _video_driver_candidates() -> list[str]:
    explicit = os.environ.get("SDL_VIDEODRIVER", "").strip()
    if explicit:
        return [explicit]

    preferred = str(config.DISPLAY_VIDEO_DRIVER or "").strip()
    has_x11_display = bool(os.environ.get("DISPLAY", "").strip())
    defaults = ["fbcon", "kmsdrm", "x11", "wayland", "directfb"]
    if has_x11_display:
        defaults = ["x11", "wayland", "fbcon", "kmsdrm", "directfb"]

    candidates: list[str] = []
    for driver in [preferred, *defaults]:
        if driver and driver not in candidates:
            candidates.append(driver)
    return candidates


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _emotion_color(label: str) -> tuple[int, int, int]:
    return {
        "joy": (255, 210, 80),
        "love": (255, 120, 180),
        "trust": (110, 240, 180),
        "fear": (120, 170, 255),
        "sadness": (90, 120, 220),
        "anger": (255, 90, 90),
        "anticipation": (255, 170, 70),
    }.get(label, (110, 200, 255))


def run_framebuffer_face(refresh_seconds: float | None = None, state_path: str | None = None) -> int:
    if pygame is None:
        print("pygame is not installed. Run 'pip install pygame' to use the framebuffer face.")
        return 1

    attempted: list[str] = []
    screen = None
    enable_scaled = _env_flag("KITEZH_DISPLAY_SCALED", default=False)
    enable_vsync = _env_flag("KITEZH_DISPLAY_VSYNC", default=False)
    for driver in _video_driver_candidates():
        os.environ["SDL_VIDEODRIVER"] = driver
        attempted.append(driver)
        try:
            pygame.display.quit()
            pygame.display.init()
            flags = pygame.FULLSCREEN
            # Some GPU/driver stacks (notably headless X11 + modeset) can produce
            # severe artifacts with SCALED. Keep it opt-in.
            if enable_scaled and hasattr(pygame, "SCALED"):
                flags |= pygame.SCALED
            if enable_vsync:
                try:
                    screen = pygame.display.set_mode((0, 0), flags, vsync=1)
                except TypeError:
                    # Older pygame signatures may not support the vsync kwarg.
                    screen = pygame.display.set_mode((0, 0), flags)
            else:
                screen = pygame.display.set_mode((0, 0), flags)
            logger.info("Framebuffer face started with SDL_VIDEODRIVER=%s", driver)
            break
        except pygame.error as exc:
            logger.warning("SDL driver '%s' unavailable: %s", driver, exc)

    if screen is None:
        print(
            "No compatible SDL video backend was available. "
            f"Tried: {', '.join(attempted)}"
        )
        return 1

    pygame.init()
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()
    interval = refresh_seconds or config.DISPLAY_REFRESH_SECONDS
    last_poll = 0.0
    state = load_display_state(state_path)

    # Draw to a fixed software canvas first, then scale to the real output size.
    # This avoids direct presentation artifacts on some X11/modeset combinations.
    logical_w, logical_h = 1280, 720
    canvas = pygame.Surface((logical_w, logical_h)).convert()

    running = True
    while running:
        now = time.time()
        try:
            for event in pygame.event.get():
                # Ignore external window-close events in kiosk/service mode.
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False

            if now - last_poll >= interval:
                state = load_display_state(state_path)
                last_poll = now

            emotion = state.get("emotion", {})
            label = str(emotion.get("label", "neutral"))
            intensity = float(emotion.get("intensity", 0.0))
            pad = emotion.get("pad", [0.0, 0.0, 0.0])
            color = _emotion_color(label)
            w, h = screen.get_size()

            cw, ch = canvas.get_size()
            canvas.fill((6, 10, 18))

            breath = 1.0 + (0.08 * math.sin(now * 1.5))
            radius = int(min(cw, ch) * (0.18 + intensity * 0.12) * breath)
            pygame.draw.circle(canvas, color, (cw // 2, ch // 2), radius)
            pygame.draw.circle(canvas, (10, 14, 24), (cw // 2, ch // 2), max(20, radius // 2))

            eye_offset_x = int(cw * 0.12)
            eye_y = int(ch * (0.42 + (0.05 * (0.5 - float(pad[1])))))
            eye_radius = max(12, int(radius * 0.18))
            for direction in (-1, 1):
                pygame.draw.circle(canvas, (240, 248, 255), (cw // 2 + direction * eye_offset_x, eye_y), eye_radius)
                pygame.draw.circle(
                    canvas,
                    (16, 20, 30),
                    (cw // 2 + direction * eye_offset_x, eye_y),
                    max(4, int(eye_radius * 0.4)),
                )

            mouth_width = int(radius * 0.7)
            mouth_y = int(ch * 0.62)
            mouth_curve = int((float(pad[0]) - 0.1) * 40)
            pygame.draw.arc(
                canvas,
                (240, 248, 255),
                pygame.Rect((cw // 2) - mouth_width // 2, mouth_y - 25, mouth_width, 50 + abs(mouth_curve)),
                math.radians(20 if mouth_curve >= 0 else 200),
                math.radians(160 if mouth_curve >= 0 else 340),
                4,
            )

            if (w, h) != (cw, ch):
                scaled = pygame.transform.smoothscale(canvas, (w, h))
                screen.blit(scaled, (0, 0))
            else:
                screen.blit(canvas, (0, 0))

            pygame.display.flip()
            clock.tick(60)
        except Exception as exc:
            logger.exception("Framebuffer face loop error; continuing: %s", exc)
            time.sleep(0.5)

    pygame.quit()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Kai's optional pygame framebuffer face.")
    parser.add_argument("--refresh", type=float, default=config.DISPLAY_REFRESH_SECONDS)
    parser.add_argument("--state-path", default=config.DISPLAY_STATE_PATH)
    args = parser.parse_args(argv)
    return run_framebuffer_face(refresh_seconds=args.refresh, state_path=args.state_path)


if __name__ == "__main__":
    raise SystemExit(main())
