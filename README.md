# K.A.I. (Kitezh Autonomous Intelligence)

K.A.I. is a standalone, bare-metal AI orchestrator designed to run entirely locally on a dedicated computing appliance. It acts as the cognitive and acoustic engine bridging a local LLM runtime (such as Ollama or Letta) with a remote FastAPI/Discord backend.

By utilizing a multi-tenant namespace router and a simulated 3D emotional state machine, K.A.I. processes external network events, shifts its internal affective momentum, and synthesizes dynamic, phase-locked formant audio in real-time.

---

## 🏗️ Core Architecture

K.A.I. is completely isolated from the remote database schema and web traffic, interacting exclusively through a secure, authenticated bridge.

*   **`network_hub.py`** — The remote HTTP gateway.
    *   **`RemoteMochiiBridge`**: A secure client enforcing `x-ai-key` headers to pull environmental context and telemetry from the external backend.
    *   **Namespace Router**: Wraps incoming webhooks into structured `UserPayload` objects and assigns `admin` or `guest` clearances.
    *   **Puppy Trap Interceptor**: Automatically flags payloads matching `DISCORD_PUPPY_ID` to trigger specialized, isolated cognitive routing protocols.
*   **`affective_core.py`** — The cognitive & vocal engine.
    *   **`PADState`**: An immutable snapshot tracking Pleasure, Arousal, and Dominance on a 3D coordinate plane `[-1.0, 1.0]`.
    *   **`AffectiveEngine`**: A stateful machine that applies exponential smoothing to emotional coordinates, simulating organic cognitive momentum over discrete time ticks.
    *   **`AudioEnvelopeWrapper`**: A numpy-backed Differentiable Digital Signal Processing (DDSP) synthesizer. It maps the PAD coordinates directly to formant filters and fundamental pitch ($f_0$) to generate real-time, cyberpunk-accented audio frames.
*   **`skills/`** — Sandboxed self-modification tools.
    *   **Filesystem Guards**: Prevents path-traversal escapes while allowing K.A.I. to patch and rewrite its own modular local Python tools.
    *   **Pre-execution Validator**: Automatically runs an AST syntax parser on AI-generated code to prevent server crashes before triggering hot-reloads.

---

## 🚀 Setup & Installation

### Prerequisites
*   Python 3.11+
*   A local LLM backend (e.g., [Ollama](https://ollama.com/) running a Qwen or Llama 3 model, or a [Letta](https://github.com/letta-ai/letta) agent).
*   A deployed remote API backend to bridge to.
*   Optional for local audio playback: `sounddevice` (the engine still runs without it).

### Initialization
```bash
# 1. Clone the repository to the bare-metal tower
git clone [https://github.com/your-org/kitezh-ai.git](https://github.com/your-org/kitezh-ai.git)
cd kitezh-ai

# 2. Setup the virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install requirements
pip install -r requirements.txt
# Optional audio dependency (for speaker playback in interactive mode)
pip install sounddevice

```
### Configuration
Create a .env file in the root directory:
```env
KITEZH_REMOTE_URL=https://your-remote-backend.com
KITEZH_AI_KEY=your_secure_ai_key
KITEZH_COMMAND_SIGNING_SECRET=your_signing_secret
KITEZH_PUPPY_ID=123456789012345678
KITEZH_LLM_BACKEND=ollama  # or 'letta'
KITEZH_OLLAMA_URL=http://localhost:11434
KITEZH_OLLAMA_MODEL=llama3
KITEZH_LETTA_URL=http://localhost:8283
KITEZH_LETTA_AGENT_ID=your_agent_id
KITEZH_WEB_PORT=7860
```

Legacy compatibility aliases are still accepted:
`MOCHII_API_URL`, `AI_BRIDGE_SECRET`, `AI_COMMAND_SIGNING_SECRET`, and `DISCORD_PUPPY_ID`.
## 💻 CLI Usage
The main.py entry point supports multiple modes for initialization, health checking, and active runtime looping.
```bash
# Pass the core system manifest to the Ollama backend
python main.py --init system_manifest.md

# Target a Letta agent backend instead
python main.py --init system_manifest.md --backend letta

# Run a secure network connectivity check against the remote backend
python main.py --health

# Start the interactive loop via the RemoteMochiiBridge
python main.py

```
```
