"""
main.py — Entry point for the Kitezh intelligence engine, now fully equipped 
with a deep cognitive BDI brain, neurochemical core, and audio synthesizer!

Usage
-----
Start the engine in interactive mode (no init file)::

    python main.py

Feed an initialization markdown file to a local LLM backend::

    python main.py --init docs/system_prompt.md
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import threading
from contextlib import nullcontext
from pathlib import Path

try:
    import sounddevice as sd
except ImportError:
    sd = None
    print("Warning: 'sounddevice' is not installed. Audio will be disabled.")
except OSError:
    sd = None
    print("Warning: 'sounddevice' could not load PortAudio. Audio will be disabled until PortAudio is installed.")

import config
from affective_core import AffectiveEngine, AudioEnvelopeWrapper, PADState
from llm_backends import send_to_backend
from network_hub import RemoteMochiiBridge, namespace_router

# Import K.A.I.'s shiny new eanchainn [brain] components!
from skills.deep_memory import DeepMemoryCore
from skills.neuro_affect import NeuroChemicalEngine
from skills.cognitive_architect import LLMCognitiveBridge
from skills.display_bridge import DisplayBridge, build_display_payload
from skills.letta_bridge import build_letta_bridge
try:
    from skills.audio_splicer import BumblebeeSplicer
except ImportError:
    BumblebeeSplicer = None
try:
    from skills.tapo_hub import TapoHub
except ImportError:
    TapoHub = None

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("kitezh.main")
if TapoHub is None:
    logger.info("Optional TapoHub dependencies are unavailable; camera hub disabled.")
if BumblebeeSplicer is None:
    logger.info("Optional audio splicer dependencies are unavailable; spliced audio disabled.")

MAX_ARCHIVED_MESSAGE_LENGTH = 200
# Maximum characters of a user message included in the Letta human-block profile summary.
LETTA_USER_MESSAGE_PREVIEW = 200
_AUTONOMY_DREAM_CONSOLIDATION_CYCLES = 24
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
DEFAULT_AUDIO_DURATION_SECONDS = 1.5
_MIN_SPOKEN_SEGMENT_SECONDS = 0.6
_MAX_SPOKEN_SEGMENT_SECONDS = 3.5
_SECONDS_PER_WORD_ESTIMATE = 0.24


def _publish_display_state(
    display_bridge: DisplayBridge,
    cognitive_bridge: LLMCognitiveBridge,
    neuro: NeuroChemicalEngine,
    *,
    mode: str,
    message: str = "",
) -> None:
    emotion = neuro.emotion_snapshot()
    payload = build_display_payload(
        emotion,
        desires=cognitive_bridge.current_desires,
        intentions=cognitive_bridge.current_intentions,
        narrative=cognitive_bridge.memory.get_self_narrative(),
        preferences=cognitive_bridge.memory.get_preferences(limit=3),
        relationship=cognitive_bridge.memory.get_relationship(neuro.active_user_id),
        mode=mode,
        message=message,
    )
    display_bridge.publish(payload)


def _start_autonomy_daemon(
    *,
    stop_event: threading.Event,
    state_lock: threading.Lock,
    engine: AffectiveEngine,
    cognitive_bridge: LLMCognitiveBridge,
    neuro: NeuroChemicalEngine,
    display_bridge: DisplayBridge,
    letta_bridge,
) -> threading.Thread:
    def _run() -> None:
        autonomy_cycles = 0
        while not stop_event.wait(config.AUTONOMY_INTERVAL_SECONDS):
            with state_lock:
                snapshot = neuro.advance_autonomous_state(config.AUTONOMY_INTERVAL_SECONDS)
                pad = snapshot["pad"]
                engine.apply_impulse(float(pad[0]), float(pad[1]), float(pad[2]))
                engine.tick()
                cognitive_bridge.refresh_self_narrative(neuro.active_user_id)
                autonomy_cycles += 1
                if autonomy_cycles % _AUTONOMY_DREAM_CONSOLIDATION_CYCLES == 0:
                    cognitive_bridge.memory.execute_dream_consolidation()
                    if letta_bridge is not None:
                        letta_bridge.send_dream_message(cognitive_bridge.memory.synthesize_personality_context())
                    _publish_display_state(
                        display_bridge,
                        cognitive_bridge,
                        neuro,
                        mode="dreaming",
                        message="Kai is consolidating its memories.",
                    )
                else:
                    _publish_display_state(
                        display_bridge,
                        cognitive_bridge,
                        neuro,
                        mode="idle",
                        message="Kai is idly reflecting.",
                    )

    thread = threading.Thread(target=_run, name="kai-autonomy", daemon=True)
    thread.start()
    return thread

def load_init_file(path: str) -> str:
    """Read and return the contents of an initialization Markdown file."""
    init_path = Path(path)
    if not init_path.exists():
        raise FileNotFoundError(f"Init file not found: '{init_path}'")
    content = init_path.read_text(encoding="utf-8")
    logger.info("Loaded init file '%s' (%d chars)", init_path, len(content))
    return content


def _estimate_segment_duration(text: str) -> float:
    # Duration estimate targets short spoken chunks:
    # - floor avoids clipped one-word responses
    # - cap avoids long single-segment playback
    # - seconds/word is a rough conversational pacing heuristic
    words = len(text.split())
    if words == 0:
        return _MIN_SPOKEN_SEGMENT_SECONDS
    return min(_MAX_SPOKEN_SEGMENT_SECONDS, max(_MIN_SPOKEN_SEGMENT_SECONDS, words * _SECONDS_PER_WORD_ESTIMATE))


def build_synthetic_splice_plan(text: str) -> list[dict[str, float | str]]:
    cleaned = text.strip()
    if not cleaned:
        return [{"type": "synthetic", "duration": 1.0}]
    chunks = [part.strip() for part in _SENTENCE_SPLIT_RE.split(cleaned) if part.strip()]
    plan: list[dict[str, float | str]] = []
    for idx, chunk in enumerate(chunks):
        plan.append({"type": "synthetic", "duration": _estimate_segment_duration(chunk)})
        if idx < len(chunks) - 1:
            plan.append({"type": "silence", "duration": 0.15})
    return plan

# ---------------------------------------------------------------------------
# Engine bootstrap
# ---------------------------------------------------------------------------

def bootstrap_engine() -> tuple[AffectiveEngine, AudioEnvelopeWrapper, LLMCognitiveBridge, NeuroChemicalEngine]:
    """Instantiate and return the core cognitive engine, audio wrapper, and deep mind."""
    # 1. The original Affective core and audio
    engine = AffectiveEngine(
        initial_state=PADState(pleasure=0.2, arousal=0.1, dominance=0.0),
        inertia=0.85,
    )
    audio = AudioEnvelopeWrapper(engine)

    # 2. Letta integration bridge (None when KITEZH_LETTA_ENABLED=0)
    letta_bridge = build_letta_bridge()

    # 3. Wire up the deep cognitive mind!
    memory = DeepMemoryCore(workspace_path=".", letta_bridge=letta_bridge)
    neuro = NeuroChemicalEngine()
    cognitive_bridge = LLMCognitiveBridge(memory, neuro)

    logger.info("AffectiveEngine and Deep Mind successfully bootstrapped!")
    return engine, audio, cognitive_bridge, neuro

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kitezh",
        description="Kitezh — modular AI orchestrator engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--init", metavar="FILE", help="Path to a Markdown initialization file.")
    parser.add_argument("--backend", choices=["ollama", "letta", "llamacpp"], default=config.LLM_BACKEND)
    parser.add_argument("--model", metavar="MODEL", default=None)
    parser.add_argument("--agent-id", metavar="ID", default=None)
    parser.add_argument("--health", action="store_true", help="Check remote backend connectivity.")
    parser.add_argument("--serve", action="store_true", help="Launch the K.A.I. web chat interface.")
    parser.add_argument("--port", type=int, default=None, metavar="PORT", help="Override the web server port (default: KITEZH_WEB_PORT / 7860).")
    parser.add_argument("--terminal-face", action="store_true", help="Render Kai's shared terminal face only.")
    parser.add_argument("--framebuffer-face", action="store_true", help="Render Kai's optional pygame framebuffer face only.")
    parser.add_argument(
        "--audio-splicer",
        action="store_true",
        help="Enable spliced audio playback pipeline for interactive replies.",
    )
    parser.add_argument(
        "--audio-library",
        metavar="DIR",
        default=None,
        help="Directory containing reusable WAV clips for audio splicer mode.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable DEBUG-level logging.")
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # Web chat server mode
    # ------------------------------------------------------------------
    if args.terminal_face:
        from skills.terminal_face import run_terminal_face
        return run_terminal_face()

    if args.framebuffer_face:
        from skills.display_face import run_framebuffer_face
        return run_framebuffer_face()

    if args.serve:
        from web_ui import start as start_web
        logger.info("Launching K.A.I. web interface on port %s…", args.port or config.WEB_PORT)
        start_web(port=args.port)
        return 0

    # ------------------------------------------------------------------
    # Health check mode
    # ------------------------------------------------------------------
    if args.health:
        if not config.REMOTE_ENABLED:
            print("Remote backend: disabled (set KITEZH_REMOTE_ENABLED=1 to enable)")
            return 0
        with RemoteMochiiBridge() as bridge:
            ok = bridge.health_check()
        status = "reachable ✓" if ok else "UNREACHABLE ✗"
        print(f"Remote backend ({config.REMOTE_BASE_URL}): {status}")
        return 0 if ok else 1

    # ------------------------------------------------------------------
    # Bootstrap cognitive engine
    # ------------------------------------------------------------------
    engine, audio, cognitive_bridge, neuro = bootstrap_engine()
    audio_splicer = None
    enable_audio_splicer = args.audio_splicer or config.AUDIO_SPLICER_ENABLED
    if enable_audio_splicer:
        if BumblebeeSplicer is None:
            logger.warning("Audio splicer requested but dependencies are unavailable; continuing with default synthesis.")
        else:
            audio_library = args.audio_library or config.AUDIO_LIBRARY_PATH
            audio_splicer = BumblebeeSplicer(sample_library_path=audio_library)
            logger.info("Audio splicer enabled using library path: %s", audio_library)
    display_bridge = DisplayBridge()
    # Grab the Letta bridge that bootstrap wired into memory (may be None).
    letta_bridge = cognitive_bridge.memory._letta
    cognitive_bridge.refresh_self_narrative()
    _publish_display_state(display_bridge, cognitive_bridge, neuro, mode="idle", message="Kai is waking up.")

    # Log a test frame to ensure the synth is working (fixed method name!)
    warmup_frame = audio.generate_frame(duration=0.1)
    logger.debug("Generated warmup audio frame (%d samples).", len(warmup_frame))
    logger.info("Audio envelope initialized properly.")

    # ------------------------------------------------------------------
    # Start Tapo camera hub (wakeword listening + autodiscovery)
    # ------------------------------------------------------------------
    tapo_hub = TapoHub(neuro=neuro) if TapoHub is not None else None
    if tapo_hub is not None:
        tapo_hub.start()

    # ------------------------------------------------------------------
    # Init-file → LLM backend
    # ------------------------------------------------------------------
    if args.init:
        try:
            prompt = load_init_file(args.init)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1

        try:
            response = send_to_backend(prompt, backend=args.backend, model=args.model, agent_id=args.agent_id)
            print("─" * 60)
            print(f"[{args.backend.upper()} RESPONSE]")
            print(response)
            print("─" * 60)
        except RuntimeError as exc:
            logger.error("%s", exc)
            return 1

    # ------------------------------------------------------------------
    # Full Cognitive Interactive Loop (stdin)
    # ------------------------------------------------------------------
    else:
        logger.info("Kitezh engine ready. Type a message or Ctrl-C to quit.")
        if not config.REMOTE_ENABLED:
            logger.info("Remote bridge disabled; interactive replies will use the %s backend.", args.backend)
        interaction_count = 0
        state_lock = threading.Lock()
        stop_event = threading.Event()
        bridge_context = RemoteMochiiBridge() if config.REMOTE_ENABLED else nullcontext(None)
        with bridge_context as bridge:
            autonomy_thread = _start_autonomy_daemon(
                stop_event=stop_event,
                state_lock=state_lock,
                engine=engine,
                cognitive_bridge=cognitive_bridge,
                neuro=neuro,
                display_bridge=display_bridge,
                letta_bridge=letta_bridge,
            )
            try:
                while True:
                    try:
                        raw = input("you › ").strip()
                    except EOFError:
                        break
                    if not raw:
                        continue
                    with state_lock:
                        payload = namespace_router(
                            platform="cli",
                            user_id="local_user",
                            display_name="Local User",
                            content=raw,
                        )
                        neuro.set_active_user(payload.user_id)

                        context_data: dict[str, object] | None = None
                        if bridge is not None:
                            ctx = bridge.query_context(payload)
                            if ctx.success:
                                print(f"kitezh › {ctx.data}")
                                context_data = ctx.data
                            else:
                                print(f"[remote error] {ctx.error}")
                                neuro.apply_stimulus(uncertainty=0.15, frustration=0.05, user_id=payload.user_id)
                                cognitive_bridge.memory.update_relationship(
                                    payload.user_id,
                                    display_name=payload.display_name,
                                    trust_delta=-0.03,
                                    tension_delta=0.04,
                                    familiarity_delta=0.01,
                                )
                                cognitive_bridge.memory.infer_preferences_from_text(raw, -0.02)
                        else:
                            try:
                                local_reply = send_to_backend(
                                    payload.content,
                                    backend=args.backend,
                                    model=args.model,
                                    agent_id=args.agent_id,
                                )
                                print(f"kitezh › {local_reply}")
                                context_data = {"reply": local_reply, "source": f"local:{args.backend}"}
                            except RuntimeError as exc:
                                print(f"[local backend error] {exc}")
                                neuro.apply_stimulus(uncertainty=0.15, frustration=0.05, user_id=payload.user_id)

                        if context_data is not None:
                            sync_payload = {
                                "user_id": payload.user_id,
                                "platform": payload.platform,
                                "content": payload.content,
                                "metadata": payload.metadata,
                                "context": context_data,
                            }
                            cognitive_bridge.synchronize_attachment(sync_payload)

                        # --- K.A.I.'S COGNITIVE PROCESS ---

                        if context_data is not None:
                            neuro.apply_stimulus(reward=0.1, success=0.2, user_id=payload.user_id)
                            cognitive_bridge.memory.update_relationship(
                                payload.user_id,
                                display_name=payload.display_name,
                                trust_delta=0.04,
                                attachment_delta=0.03,
                                familiarity_delta=0.05,
                                tension_delta=-0.02,
                            )
                            cognitive_bridge.memory.infer_preferences_from_text(raw, 0.03)
                            reply_text = str(context_data.get("reply", context_data))
                            cognitive_bridge.memory.infer_preferences_from_text(reply_text, 0.02)

                        pad_coords = neuro.get_pad_coordinates()
                        engine.apply_impulse(pad_coords[0], pad_coords[1], pad_coords[2])
                        engine.tick()
                        emotion_snapshot = neuro.emotion_snapshot(pad=pad_coords)
                        logger.debug("Affective state updated: %s label=%s", engine.current_state, emotion_snapshot["label"])

                        intensity = neuro.emotional_intensity(pad=pad_coords)
                        importance = 1.0 + intensity
                        memory_type = "key" if intensity >= 0.6 else "episodic"
                        archived_user_content = f"User: {raw}"[:MAX_ARCHIVED_MESSAGE_LENGTH]
                        cognitive_bridge.memory.archive_episode(
                            category="conversation",
                            content=archived_user_content,
                            p=float(pad_coords[0]),
                            a=float(pad_coords[1]),
                            d=float(pad_coords[2]),
                            importance=importance,
                            memory_type=memory_type,
                        )
                        logger.debug(
                            "Archived interaction (intensity=%.2f, type=%s)", intensity, memory_type
                        )

                        cognitive_bridge.deliberate()

                        if letta_bridge is not None:
                            letta_bridge.update_human_block(
                                f"Active user: {payload.display_name} (id={payload.user_id}). "
                                f"Most recent message: {raw[:LETTA_USER_MESSAGE_PREVIEW]}"
                            )

                        interaction_count += 1
                        if interaction_count % 10 == 0:
                            cognitive_bridge.memory.execute_dream_consolidation()
                            logger.info("Dream consolidation complete after %d interactions.", interaction_count)
                            if letta_bridge is not None:
                                personality_ctx = cognitive_bridge.memory.synthesize_personality_context()
                                letta_bridge.send_dream_message(personality_ctx)
                            _publish_display_state(
                                display_bridge,
                                cognitive_bridge,
                                neuro,
                                mode="dreaming",
                                message="Kai is consolidating its memories.",
                            )
                        else:
                            _publish_display_state(
                                display_bridge,
                                cognitive_bridge,
                                neuro,
                                mode="active",
                                message=f"Speaking with {payload.display_name}.",
                            )

                        if sd is not None:
                            text_for_audio = reply_text if context_data is not None else ""
                            if audio_splicer is not None:
                                splice_plan = build_synthetic_splice_plan(text_for_audio)
                                wave_data = audio_splicer.splice_sequence(splice_plan, synthetic_voice_generator=audio.generate_frame)
                                if len(wave_data) == 0:
                                    wave_data = audio.generate_frame(duration=DEFAULT_AUDIO_DURATION_SECONDS)
                            else:
                                wave_data = audio.generate_frame(duration=DEFAULT_AUDIO_DURATION_SECONDS)
                            sd.play(wave_data, 44100)
                            sd.wait()

            except KeyboardInterrupt:
                print("\nGoodbye.")
            finally:
                stop_event.set()
                autonomy_thread.join(timeout=config.AUTONOMY_SHUTDOWN_TIMEOUT_SECONDS)
                _publish_display_state(display_bridge, cognitive_bridge, neuro, mode="idle", message="Kai is resting.")
                if tapo_hub is not None:
                    tapo_hub.stop()

    return 0

if __name__ == "__main__":
    sys.exit(main())
