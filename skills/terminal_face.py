from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from typing import Any

import config
from skills.display_bridge import load_display_state

logger = logging.getLogger(__name__)

_HIGH_INTENSITY_THRESHOLD = 0.55
_MEDIUM_INTENSITY_THRESHOLD = 0.25


def _terminal_size() -> tuple[int, int]:
    size = shutil.get_terminal_size(fallback=(80, 24))
    return max(40, size.columns), max(20, size.lines)


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
    return f"screen   {mode[:8]:<8} -> {target[:22]:<22} ({updated_by[:8]})"


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


def _ansi_bg(label: str) -> str:
    return "\033[40m"


def _emotion_profile(label: str, intensity: float) -> tuple[str, str, str, str]:
    if label == "joy":
        return ("bright", "^  ^", "\\__/", "Kai is glowing")
    if label == "love":
        return ("soft", "^  ^", "\_/", "Kai is tender")
    if label == "trust":
        return ("steady", "o  o", "\__", "Kai is settled")
    if label == "fear":
        return ("tense", "O  O", "____", "Kai is braced")
    if label == "sadness":
        return ("low", "u  u", "/__\\", "Kai is heavy")
    if label == "anger":
        return ("hot", ">  <" if intensity > 0.4 else "o  o", "----", "Kai is sharp")
    if label == "anticipation":
        return ("charged", "o  o", "\_/-", "Kai is leaning in")
    return ("calm", "o  o", "\--/", "Kai is listening")


def render_terminal_face(state: dict[str, Any]) -> str:
    emotion = state.get("emotion", {})
    label = str(emotion.get("label", "neutral"))
    intensity = float(emotion.get("intensity", 0.0))
    narrative = str(state.get("narrative", "Kai is quiet but present."))
    strongest_need = str(emotion.get("strongest_need", "connection"))
    message = str(state.get("message", ""))
    pad = emotion.get("pad", [0.0, 0.0, 0.0])
    pad_text = str([round(float(v), 2) for v in pad])
    cols, rows = _terminal_size()
    mood_band, eyes, mouth, mood_phrase = _emotion_profile(label, intensity)
    if intensity > _HIGH_INTENSITY_THRESHOLD and label not in {"fear", "sadness"}:
        eyes = "◕ ◕"
    if intensity > 0.75 and label == "anger":
        mouth = "⌒"
    fg = "\033[97m"
    bg = _ansi_bg(label)
    reset = "\033[0m"
    top = "\033[?25l\033[2J\033[H"
    rows_out: list[str] = [top]

    def line_fill(text: str = "") -> str:
        plain = text[:cols]
        if len(plain) < cols:
            plain = plain + (" " * (cols - len(plain)))
        return f"{bg}{fg}{plain}{reset}\n"

    rows_out.append(line_fill())
    rows_out.append(line_fill(f"{ 'K.A.I. ASCII FACE':^{cols} }"))
    rows_out.append(line_fill())

    face_block = [
        f"{mood_phrase:^{cols}}",
        f"{eyes:^8}",
        f"{mouth:^8}",
        "",
        f"emotion   {label} / {mood_band}",
        f"need      {strongest_need}",
        f"pad       {pad_text}",
        _screen_line(state),
    ]
    face_start = max(4, rows // 3)
    for row_index in range(4, rows - 4):
        rel = row_index - face_start
        if 0 <= rel < len(face_block):
            text = face_block[rel]
            centered = f"{text:^{cols}}"
            rows_out.append(line_fill(centered))
        elif row_index == rows - 5:
            rows_out.append(line_fill(f"{narrative[:cols]:^{cols}}"))
        elif row_index == rows - 4:
            rows_out.append(line_fill(f"{message[:cols]:^{cols}}"))
        else:
            rows_out.append(line_fill())

    rows_out.append(line_fill())
    rows_out.append(line_fill(f"{ 'mode: ' + label :^{cols} }"))
    rows_out.append(line_fill())
    rows_out.append(reset + "\033[?25h")
    return "".join(rows_out)


def run_terminal_face(refresh_seconds: float | None = None, state_path: str | None = None) -> int:
    interval = refresh_seconds or config.DISPLAY_REFRESH_SECONDS
    last_version = None
    logged_write_failure = False
    try:
        sys.stdout.write("\033[?1049h\033[2J\033[H")
        sys.stdout.flush()
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
    finally:
        try:
            sys.stdout.write("\033[0m\033[?25h\033[?1049l")
            sys.stdout.flush()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render Kai's terminal face from the shared display state.")
    parser.add_argument("--refresh", type=float, default=config.DISPLAY_REFRESH_SECONDS)
    parser.add_argument("--state-path", default=config.DISPLAY_STATE_PATH)
    args = parser.parse_args(argv)
    return run_terminal_face(refresh_seconds=args.refresh, state_path=args.state_path)


if __name__ == "__main__":
    raise SystemExit(main())
