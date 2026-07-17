#!/usr/bin/env bash
# llama_server_gpu.sh — Launch llama-server with full NVIDIA/Metal GPU offload.
#
# Switch to this profile when a GPU is available. No Kitezh code changes needed —
# both scripts expose the same OpenAI-compatible endpoint on the same host/port.
#
# Hardware budget (Q4_K_M Nous-Hermes-2-Mixtral-8x7B-DPO):
#   NVIDIA: ~26 GB VRAM for full offload (2x RTX 3090 / A100 / RTX 4090, etc.)
#   Partial offload (fewer layers) works with less VRAM at a speed cost.
#   Apple Silicon: set n-gpu-layers to the layer count of the model (~33 for Mixtral 8x7B).
#
# Usage:
#   export LLAMA_MODEL=/path/to/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf
#   ./scripts/llama_server_gpu.sh
#
# Tune LLAMA_GPU_LAYERS to match your available VRAM:
#   Full offload : 99 (llama.cpp treats any large value as "all layers")
#   Half offload : ~17 (roughly half the expert layers in VRAM)
#   CPU fallback : 0  (use llama_server_cpu.sh instead)

set -euo pipefail

MODEL="${LLAMA_MODEL:-/models/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf}"
HOST="${LLAMA_HOST:-127.0.0.1}"
PORT="${LLAMA_PORT:-8080}"
CTX="${LLAMA_CTX:-8192}"
GPU_LAYERS="${LLAMA_GPU_LAYERS:-99}"
# For GPU mode, fewer threads are needed for CPU-side scheduling
THREADS="${LLAMA_THREADS:-4}"

echo "🦙 Starting llama-server in GPU offload mode"
echo "   Model      : ${MODEL}"
echo "   Host       : ${HOST}:${PORT}"
echo "   Context    : ${CTX} tokens"
echo "   GPU layers : ${GPU_LAYERS}"
echo "   CPU threads: ${THREADS}"
echo ""

exec llama-server \
  --model "${MODEL}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --ctx-size "${CTX}" \
  --n-gpu-layers "${GPU_LAYERS}" \
  --threads "${THREADS}" \
  --chat-template chatml
