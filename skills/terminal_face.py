from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any

import config
from skills.display_bridge import load_display_state

logger = logging.getLogger(__name__)

_HIGH_INTENSITY_THRESHOLD = 0.55
_MEDIUM_INTENSITY_THRESHOLD = 0.25


def _screen_line(state: dict[str, Any]) -> str:
    screen = state.get("screen", {}) if isinstance(state.get("screen", {}), dict) else {}
    mode = str(screen.get("mode", "face"))
    url = str(screen.get("url", "/face"))
    updated_by = str(screen.get("updated_by", "system"))
    if mode == "url":
        target = url
    elif mode == "ui":
        target = "workspace ui"
    else:
        target = "face"
    return f"screen   {mode[:8]:<8} → {target[:22]:<22} ({updated_by[:8]})"


def _ansi_color(label: str) -> str:
    return {
        "joy": "\033[38;5;220m",
        "love": "\033[38;5;213m",
        "trust": "\033[38;5;121m",
        "fear": "\033[38;5;111m",
        "sadness": "\033[38;5;75m",
        "anger": "\033[38;5;203m",
        "anticipation": "\033[38;5;214m",
    }.get(label, "\033[38;5;117m")


def render_terminal_face(state: dict[str, Any]) -> str:
    emotion = state.get("emotion", {})
    label = str(emotion.get("label", "neutral"))
    intensity = float(emotion.get("intensity", 0.0))
    narrative = str(state.get("narrative", "Kai is quiet but present."))
    strongest_need = str(emotion.get("strongest_need", "connection"))
    message = str(state.get("message", ""))
    pad = emotion.get("pad", [0.0, 0.0, 0.0])
    pad_text = str([round(float(v), 2) for v in pad])
    eyes = (
        "◕ ◕"
        if intensity > _HIGH_INTENSITY_THRESHOLD
        else "◔ ◔" if intensity > _MEDIUM_INTENSITY_THRESHOLD else "• •"
    )
    mouth = "_" if label in {"fear", "sadness"} else "‿" if label in {"joy", "love", "trust"} else "—"
    color = _ansi_color(label)
    reset = "\033[0m"
    clear = "\033[2J\033[H"
    return (
        f"{clear}{color}"
        "╔════════════════════════════════════════╗\n"
        "║                K.A.I.                 ║\n"
        "║                                        ║\n"
        f"║                {eyes:^8}                ║\n"
        f"║                 {mouth:^4}                 ║\n"
        "║                                        ║\n"
        f"║ emotion   {label[:28]:<28}║\n"
        f"║ need      {strongest_need[:28]:<28}║\n"
        f"║ pad       {pad_text[:28]:<28}║\n"
        f"║ {_screen_line(state)[:38]:<38} ║\n"
        "╠════════════════════════════════════════╣\n"
        f"║ {narrative[:38]:<38} ║\n"
        f"║ {message[:38]:<38} ║\n"
        "╚════════════════════════════════════════╝\n"
        f"{reset}"
    )


def run_terminal_face(refresh_seconds: float | None = None, state_path: str | None = None) -> int:
    interval = refresh_seconds or config.DISPLAY_REFRESH_SECONDS
    last_version = None
    logged_write_failure = False
    try:
        while True:
            state = load_display_state(state_path)
            version = state.get("version")
            if version != last_version:
                frame = render_terminal_face(state)
                try:
                    sys.stdout.write(frame)
                    sys.stdout.flush()
                    logged_write_failure = False
                except (BrokenPipeError, OSError) as exc:
                    if not logged_write_failure:
                        logger.warning("Terminal face output failed; will keep retrying: %s", exc)
                        logged_write_failure = True
                last_version = version
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Kai's terminal face from the shared display state.")
    parser.add_argument("--refresh", type=float, default=config.DISPLAY_REFRESH_SECONDS)
    parser.add_argument("--state-path", default=config.DISPLAY_STATE_PATH)
    args = parser.parse_args(argv)
    return run_terminal_face(refresh_seconds=args.refresh, state_path=args.state_path)


if __name__ == "__main__":
    raise SystemExit(main())
