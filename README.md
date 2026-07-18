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
*   A local LLM backend (e.g., [Ollama](https://ollama.com/), [Letta](https://github.com/letta-ai/letta), or [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server`).
*   A deployed remote API backend to bridge to.
*   Optional for local audio playback: `sounddevice` (the engine still runs without it).
    * If it is missing, interactive mode still works and logs a warning while skipping speaker output.
*   Optional for Tapo camera wakeword listening: `openwakeword`.
    * On Linux this may be unavailable on newer Python releases because `tflite-runtime` wheels are not published for every interpreter version. If it is missing, wakeword listeners stay disabled while the rest of Kitezh continues to run.

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
# Optional wakeword dependency (skip if openwakeword/tflite-runtime is unavailable on your Python/platform)
pip install -r requirements-wakeword.txt

```
### Configuration
Create a .env file in the root directory:
```env
KITEZH_REMOTE_ENABLED=0  # set to 1 to use the external API bridge

# Only required when KITEZH_REMOTE_ENABLED=1
KITEZH_REMOTE_URL=https://your-remote-backend.com
KITEZH_AI_KEY=your_secure_ai_key
KITEZH_COMMAND_SIGNING_SECRET=your_signing_secret

KITEZH_PUPPY_ID=123456789012345678
KITEZH_LLM_BACKEND=ollama  # or 'letta' / 'llamacpp'
KITEZH_OLLAMA_URL=http://localhost:11434
KITEZH_OLLAMA_MODEL=llama3
KITEZH_LETTA_URL=http://localhost:8283
KITEZH_LETTA_AGENT_ID=your_agent_id
KITEZH_LLAMACPP_URL=http://localhost:8080
KITEZH_LLAMACPP_MODEL=nous-hermes-2-mixtral-8x7b-dpo-gguf
KITEZH_WEB_PORT=7860
KITEZH_AUDIO_SPLICER_ENABLED=0
KITEZH_AUDIO_LIBRARY_PATH=./workspace/audio_library
```

Legacy compatibility aliases are still accepted:
`MOCHII_API_URL`, `AI_BRIDGE_SECRET`, `AI_COMMAND_SIGNING_SECRET`, and `DISCORD_PUPPY_ID`.

With `KITEZH_REMOTE_ENABLED=0`, web chat and CLI interactive mode fall back to
the configured local LLM backend instead of requiring the external API.

---

## 🌐 Network Roles (Dual-homing)

K.A.I. is designed for a bare-metal appliance with two network interfaces:

| Interface | Role | Traffic |
|---|---|---|
| **Wi-Fi** | Internet uplink | Remote bridge calls, LLM API, cloudflared tunnel |
| **Ethernet** | Local device LAN | Admin web UI, camera streams, local hardware |

### How it works

Public internet users reach the chat interface through a **cloudflared** tunnel.
`cloudflared` runs on the same machine and connects to Kitezh via loopback
(`127.0.0.1:7860`), so from Uvicorn's perspective the source IP is always loopback.

Operator admin access comes directly over the Ethernet NIC, so the source IP is
a real address on the local subnet.

The `LanAdminGuard` middleware uses this to enforce separation:

- **Loopback source** (i.e. cloudflared / public internet) → **403** for any `/api/kai/*` route.
- **LAN source within `KITEZH_LAN_CIDR`** → allowed through to admin endpoints.
- **Any other source** → **403**.
- **`KITEZH_LAN_CIDR` unset** → guard is a no-op (useful for local development).

Public routes (`/`, `/ws/*`, `/api/chat/*`, `/api/kai/emotion`) are reachable on
**both** interfaces regardless of this setting.

### Configuration

Add to your `.env`:

```env
# Your Ethernet LAN subnet in CIDR notation
KITEZH_LAN_CIDR=192.168.1.0/24

# Optionally override the admin path prefix (default: /api/kai)
KITEZH_ADMIN_PATH_PREFIX=/api/kai
```

### cloudflared setup (outline)

Configure the tunnel ingress to point at the local server:

```yaml
# ~/.cloudflared/config.yml
tunnel: <your-tunnel-id>
credentials-file: /home/user/.cloudflared/<tunnel-id>.json

ingress:
  - hostname: chat.yourdomain.com
    service: http://localhost:7860
  - service: http_status:404
```

Start the tunnel:

```bash
cloudflared tunnel run <your-tunnel-name>
```

Public users reach `https://chat.yourdomain.com`.  Admin routes remain
exclusively accessible from the Ethernet LAN.


## 💻 CLI Usage
The main.py entry point supports multiple modes for initialization, health checking, and active runtime looping.
```bash
# Pass the core system manifest to the Ollama backend
python main.py --init system_manifest.md

# Target a Letta agent backend instead
python main.py --init system_manifest.md --backend letta

# Target a llama.cpp OpenAI-compatible backend instead
python main.py --init system_manifest.md --backend llamacpp --model nous-hermes-2-mixtral-8x7b-dpo-gguf

# Run a secure network connectivity check against the remote backend
python main.py --health

# Start the interactive loop via the RemoteMochiiBridge
python main.py

# Enable stitched reply playback (synthetic segments + pauses)
python main.py --audio-splicer

# Use a custom reusable clip library path for splicer mode
python main.py --audio-splicer --audio-library /path/to/audio_library

```

## 🦙 llama.cpp + Nous Hermes 2 setup

Use this path when running `Nous-Hermes-2-Mixtral-8x7B-DPO` in GGUF format through `llama-server`.
The `scripts/` directory provides two ready-made launch profiles. Both expose the same endpoint
(`http://localhost:8080/v1`) so **switching between CPU and GPU requires no Kitezh code changes** —
only a script swap and a server restart.

### CPU / RAM-only profile (current default)

```bash
export LLAMA_MODEL=/models/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf
./scripts/llama_server_cpu.sh
```

Key flags set by this script:
| Flag | Value | Notes |
|---|---|---|
| `--n-gpu-layers` | `0` | All layers run in RAM, no VRAM needed |
| `--threads` | `$(nproc)` | Auto-detected physical core count |
| `--ctx-size` | `8192` | Override with `LLAMA_CTX=<n>` |

Hardware requirement: ~26 GB system RAM for Q4_K_M Mixtral 8x7B.

### GPU offload profile (future)

```bash
export LLAMA_MODEL=/models/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf
./scripts/llama_server_gpu.sh
```

Key flags set by this script:
| Flag | Value | Notes |
|---|---|---|
| `--n-gpu-layers` | `99` (all) | Override with `LLAMA_GPU_LAYERS=<n>` for partial offload |
| `--threads` | `4` | CPU threads for scheduling only |
| `--ctx-size` | `8192` | Override with `LLAMA_CTX=<n>` |

Hardware requirement: ~26 GB VRAM for full offload. Partial offload (fewer `LLAMA_GPU_LAYERS`)
works with less VRAM at a speed cost.

### Shared env vars

Both scripts honour the same environment variables so they can be sourced from a common `.env`:

```env
LLAMA_MODEL=/models/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
LLAMA_CTX=8192
# CPU mode: set LLAMA_THREADS to physical core count
# GPU mode: set LLAMA_GPU_LAYERS (default 99 = full offload)
```

### Quick smoke test

Run this after either script starts to verify the server is ready before launching Kitezh:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"nous-hermes-2-mixtral-8x7b-dpo-gguf","messages":[{"role":"user","content":"hello"}]}'
```

### Letta → llama-server bridge

`llama-server` is OpenAI-compatible, so Letta can point to `http://localhost:8080/v1` in its
`LLMConfig`. This works identically regardless of whether the CPU or GPU profile is active.
