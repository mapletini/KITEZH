#!/usr/bin/env bash
# llama_server_cpu.sh — Launch llama-server in CPU/RAM-only mode.
#
# This is the current default profile while a dedicated GPU is not available.
# All model layers run in system RAM; no VRAM is required.
#
# To switch to GPU mode when hardware is ready, use llama_server_gpu.sh instead.
# Kitezh requires no code changes — just swap which script is running.
#
# Hardware budget (Q4_K_M Nous-Hermes-2-Mixtral-8x7B-DPO):
#   ~26 GB RAM required for full model load.
#
# Usage:
#   export LLAMA_MODEL=/path/to/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf
#   ./scripts/llama_server_cpu.sh
#
# Or inline:
#   LLAMA_MODEL=/path/to/model.gguf ./scripts/llama_server_cpu.sh

set -euo pipefail

MODEL="${LLAMA_MODEL:-/models/Nous-Hermes-2-Mixtral-8x7B-DPO.Q4_K_M.gguf}"
HOST="${LLAMA_HOST:-127.0.0.1}"
PORT="${LLAMA_PORT:-8080}"
CTX="${LLAMA_CTX:-8192}"
# Physical core count — set LLAMA_THREADS to override (hyperthreads are slower for inference)
THREADS="${LLAMA_THREADS:-$(nproc --ignore=1 2>/dev/null || sysctl -n hw.physicalcpu 2>/dev/null || echo 4)}"

echo "🦙 Starting llama-server in CPU/RAM mode"
echo "   Model  : ${MODEL}"
echo "   Host   : ${HOST}:${PORT}"
echo "   Context: ${CTX} tokens"
echo "   Threads: ${THREADS}"
echo "   GPU layers: 0 (all layers in RAM)"
echo ""

exec llama-server \
  --model "${MODEL}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --ctx-size "${CTX}" \
  --n-gpu-layers 0 \
  --threads "${THREADS}" \
  --chat-template chatml
