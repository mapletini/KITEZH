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
import sys
from contextlib import nullcontext
from pathlib import Path

try:
    import sounddevice as sd
except ImportError:
    sd = None
    print("Warning: 'sounddevice' module not found. Audio will be disabled. Run 'pip install sounddevice numpy'")

import config
from affective_core import AffectiveEngine, AudioEnvelopeWrapper, PADState
from llm_backends import send_to_backend
from network_hub import RemoteMochiiBridge, namespace_router

# Import K.A.I.'s shiny new eanchainn [brain] components!
from skills.deep_memory import DeepMemoryCore
from skills.neuro_affect import NeuroChemicalEngine
from skills.cognitive_architect import LLMCognitiveBridge
from skills.tapo_hub import TapoHub

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("kitezh.main")

MAX_ARCHIVED_MESSAGE_LENGTH = 200

def load_init_file(path: str) -> str:
    """Read and return the contents of an initialization Markdown file."""
    init_path = Path(path)
    if not init_path.exists():
        raise FileNotFoundError(f"Init file not found: '{init_path}'")
    content = init_path.read_text(encoding="utf-8")
    logger.info("Loaded init file '%s' (%d chars)", init_path, len(content))
    return content

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
    
    # 2. Wire up the deep cognitive mind!
    memory = DeepMemoryCore(workspace_path=".")
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

    # Log a test frame to ensure the synth is working (fixed method name!)
    audio.generate_frame(duration=0.1)
    logger.info("Audio envelope initialized properly.")

    # ------------------------------------------------------------------
    # Start Tapo camera hub (wakeword listening + autodiscovery)
    # ------------------------------------------------------------------
    tapo_hub = TapoHub(neuro=neuro)
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
        bridge_context = RemoteMochiiBridge() if config.REMOTE_ENABLED else nullcontext(None)
        with bridge_context as bridge:
            try:
                while True:
                    try:
                        raw = input("you › ").strip()
                    except EOFError:
                        break
                    if not raw:
                        continue
                        
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

                    # 1. Trigger a chemical reaction based on successful communication
                    if context_data is not None:
                        neuro.apply_stimulus(reward=0.1, success=0.2, user_id=payload.user_id)

                    # 2. Convert raw chemicals into PAD coordinates and push them to the engine
                    pad_coords = neuro.get_pad_coordinates()
                    engine.apply_impulse(pad_coords[0], pad_coords[1], pad_coords[2])

                    # 3. Advance the affective engine tick to calculate the momentum drift
                    engine.tick()
                    emotion_snapshot = neuro.emotion_snapshot(pad=pad_coords)
                    logger.debug("Affective state updated: %s label=%s", engine.current_state, emotion_snapshot["label"])

                    # 4. Archive this interaction as a memory.
                    #    Emotional intensity determines whether it becomes a key (flashbulb) memory.
                    intensity = neuro.emotional_intensity(pad=pad_coords)
                    importance = 1.0 + intensity  # higher emotion → more important
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

                    # 5. Trigger the BDI Prefrontal Cortex to deliberate
                    cognitive_bridge.deliberate()

                    # 6. Every 10 interactions run dream consolidation (fidelity/synapse decay)
                    interaction_count += 1
                    if interaction_count % 10 == 0:
                        cognitive_bridge.memory.execute_dream_consolidation()
                        logger.info("Dream consolidation complete after %d interactions.", interaction_count)

                    # 7. Play the cyber lilt audio out loud
                    if sd is not None:
                        wave_data = audio.generate_frame(duration=1.5)
                        sd.play(wave_data, 44100)
                        sd.wait()

            except KeyboardInterrupt:
                print("\nGoodbye.")
            finally:
                tapo_hub.stop()

    return 0

if __name__ == "__main__":
    sys.exit(main())
