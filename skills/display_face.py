from __future__ import annotations

import argparse
import math
import os
import time

import config
from skills.display_bridge import load_display_state

try:
    import pygame
except ImportError:  # pragma: no cover - optional dependency
    pygame = None


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

    os.environ.setdefault("SDL_VIDEODRIVER", "kmsdrm")
    pygame.init()
    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    clock = pygame.time.Clock()
    interval = refresh_seconds or config.DISPLAY_REFRESH_SECONDS
    last_poll = 0.0
    state = load_display_state(state_path)

    running = True
    while running:
        now = time.time()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
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
        screen.fill((6, 10, 18))

        breath = 1.0 + (0.08 * math.sin(now * 1.5))
        radius = int(min(w, h) * (0.18 + intensity * 0.12) * breath)
        pygame.draw.circle(screen, color, (w // 2, h // 2), radius)
        pygame.draw.circle(screen, (10, 14, 24), (w // 2, h // 2), max(20, radius // 2))

        eye_offset_x = int(w * 0.12)
        eye_y = int(h * (0.42 + (0.05 * (0.5 - float(pad[1])))))
        eye_radius = max(12, int(radius * 0.18))
        for direction in (-1, 1):
            pygame.draw.circle(screen, (240, 248, 255), (w // 2 + direction * eye_offset_x, eye_y), eye_radius)
            pygame.draw.circle(
                screen,
                (16, 20, 30),
                (w // 2 + direction * eye_offset_x, eye_y),
                max(4, int(eye_radius * 0.4)),
            )

        mouth_width = int(radius * 0.7)
        mouth_y = int(h * 0.62)
        mouth_curve = int((float(pad[0]) - 0.1) * 40)
        pygame.draw.arc(
            screen,
            (240, 248, 255),
            pygame.Rect((w // 2) - mouth_width // 2, mouth_y - 25, mouth_width, 50 + abs(mouth_curve)),
            math.radians(20 if mouth_curve >= 0 else 200),
            math.radians(160 if mouth_curve >= 0 else 340),
            4,
        )

        pygame.display.flip()
        clock.tick(30)

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
