#!/usr/bin/env bash
# setup.sh — Interactive first-run setup for the K.A.I. (Kitezh) engine.
#
# What this script does:
#   1. Verifies Python 3.11+ is available
#   2. Creates a virtual environment (.venv) if one does not exist
#   3. Installs all dependencies from requirements.txt
#   4. Generates a .env file (from .env.example) if one does not exist
#   5. Walks through the key configuration values interactively
#   6. When the Letta backend is chosen, ensures the Kai agent will be
#      auto-created on first engine launch (no manual agent ID needed).
#
# Usage:
#   chmod +x scripts/setup.sh
#   ./scripts/setup.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
ENV_EXAMPLE="${REPO_ROOT}/.env.example"
VENV_DIR="${REPO_ROOT}/.venv"
HEADLESS_LINUX=0

if [[ "$(uname -s)" == "Linux" ]] && [[ -z "${DISPLAY:-}" ]] && [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    HEADLESS_LINUX=1
fi

# ── Colour / output helpers ───────────────────────────────────────────────────

bold()  { printf "\033[1m%s\033[0m" "$*"; }
green() { printf "\033[32m%s\033[0m" "$*"; }
yellow(){ printf "\033[33m%s\033[0m" "$*"; }
red()   { printf "\033[31m%s\033[0m" "$*"; }

info()  { echo "  → $*"; }
ok()    { echo "  $(green "✓") $*"; }
warn()  { echo "  $(yellow "⚠") $*"; }
die()   { echo "  $(red "✗") $*" >&2; exit 1; }

# Prompt with an optional default.  Returns the user's input (or default).
ask() {
    local prompt="$1"
    local default="${2:-}"
    local answer
    if [[ -n "$default" ]]; then
        read -rp "  $(bold "${prompt}") [${default}]: " answer
        echo "${answer:-$default}"
    else
        read -rp "  $(bold "${prompt}"): " answer
        echo "$answer"
    fi
}

# Yes/no prompt — returns 0 for yes, 1 for no.
ask_yn() {
    local prompt="$1"
    local default="${2:-y}"   # y or n
    local hint
    if [[ "${default,,}" == "y" ]]; then hint="Y/n"; else hint="y/N"; fi
    local answer
    read -rp "  $(bold "${prompt}") [${hint}]: " answer
    answer="${answer:-$default}"
    [[ "${answer,,}" =~ ^(y|yes)$ ]]
}

# Replace (or append) a KEY=VALUE line in the .env file.
# Uses a temp-file pattern so both GNU sed and macOS sed are supported.
set_env() {
    local key="$1"
    local value="$2"
    if grep -qE "^${key}=" "${ENV_FILE}" 2>/dev/null; then
        local tmp
        tmp="$(mktemp)"
        sed "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}" > "$tmp" && mv "$tmp" "${ENV_FILE}"
    else
        echo "${key}=${value}" >> "${ENV_FILE}"
    fi
}

# ── Banner ────────────────────────────────────────────────────────────────────

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║           K.A.I. (Kitezh) — First-Run Setup Script           ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# ── Step 1: Python version ────────────────────────────────────────────────────

echo "$(bold "[ 1/5 ]") Checking Python version…"

PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || true)
        major=$("$candidate" -c 'import sys; print(sys.version_info[0])' 2>/dev/null || echo 0)
        minor=$("$candidate" -c 'import sys; print(sys.version_info[1])' 2>/dev/null || echo 0)
        if (( major >= 3 && minor >= 11 )); then
            PYTHON="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    die "Python 3.11 or newer is required but was not found. Install it and re-run this script."
fi

ok "Using $($PYTHON --version)"
echo ""

# ── Step 2: Virtual environment ───────────────────────────────────────────────

echo "$(bold "[ 2/5 ]") Setting up virtual environment…"

if [[ -d "${VENV_DIR}" ]]; then
    ok "Virtual environment already exists at .venv — skipping creation."
else
    info "Creating .venv with ${PYTHON}…"
    "$PYTHON" -m venv "${VENV_DIR}"
    ok "Virtual environment created."
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"
echo ""

# ── Step 3: Install dependencies ─────────────────────────────────────────────

echo "$(bold "[ 3/5 ]") Installing Python dependencies…"
info "Running: pip install -r requirements.txt"
pip install --quiet --upgrade pip
pip install --quiet -r "${REPO_ROOT}/requirements.txt"
ok "Dependencies installed."
echo ""

# ── Step 4: Generate .env ─────────────────────────────────────────────────────

echo "$(bold "[ 4/5 ]") Environment configuration…"

if [[ -f "${ENV_FILE}" ]]; then
    warn ".env already exists.  Skipping copy from .env.example."
    warn "To reconfigure, delete .env and re-run this script, or edit it manually."
else
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
    ok "Created .env from .env.example."
fi

echo ""
echo "  Let's configure the key values.  Press Enter to keep the current/default."
echo ""

# ── LLM backend ───────────────────────────────────────────────────────────────

current_backend=$(grep -E "^KITEZH_LLM_BACKEND=" "${ENV_FILE}" | cut -d= -f2 || echo "llamacpp")
echo "  LLM backend choices: $(bold ollama) | $(bold letta) | $(bold llamacpp)"
backend=$(ask "KITEZH_LLM_BACKEND" "${current_backend}")
set_env "KITEZH_LLM_BACKEND" "${backend}"

case "${backend}" in
    ollama)
        current_url=$(grep -E "^KITEZH_OLLAMA_URL=" "${ENV_FILE}" | cut -d= -f2 || echo "http://localhost:11434")
        current_model=$(grep -E "^KITEZH_OLLAMA_MODEL=" "${ENV_FILE}" | cut -d= -f2 || echo "llama3")
        ollama_url=$(ask "KITEZH_OLLAMA_URL" "${current_url}")
        ollama_model=$(ask "KITEZH_OLLAMA_MODEL" "${current_model}")
        set_env "KITEZH_OLLAMA_URL" "${ollama_url}"
        set_env "KITEZH_OLLAMA_MODEL" "${ollama_model}"
        ;;
    letta)
        current_url=$(grep -E "^KITEZH_LETTA_URL=" "${ENV_FILE}" | cut -d= -f2 || echo "http://localhost:8283")
        letta_url=$(ask "KITEZH_LETTA_URL" "${current_url}")
        set_env "KITEZH_LETTA_URL" "${letta_url}"
        set_env "KITEZH_LETTA_ENABLED" "1"
        # Default agent name — leave KITEZH_LETTA_AGENT_ID empty so the engine
        # auto-creates (or finds) an agent named after KITEZH_LETTA_AGENT_NAME.
        current_agent_name=$(grep -E "^KITEZH_LETTA_AGENT_NAME=" "${ENV_FILE}" | cut -d= -f2 || echo "kai")
        agent_name=$(ask "KITEZH_LETTA_AGENT_NAME (default Letta agent)" "${current_agent_name}")
        set_env "KITEZH_LETTA_AGENT_NAME" "${agent_name}"
        # Only ask for an explicit agent ID if the user wants to target one that already exists.
        if ask_yn "Do you already have a Letta agent ID to use?" "n"; then
            agent_id=$(ask "KITEZH_LETTA_AGENT_ID")
            set_env "KITEZH_LETTA_AGENT_ID" "${agent_id}"
        else
            set_env "KITEZH_LETTA_AGENT_ID" ""
            ok "Agent ID left blank — Kai will be auto-created on first launch."
        fi
        current_token=$(grep -E "^KITEZH_LETTA_TOKEN=" "${ENV_FILE}" | cut -d= -f2 || echo "")
        if [[ -n "${current_token}" ]]; then
            letta_token=$(ask "KITEZH_LETTA_TOKEN (leave blank for unauthenticated local server)" "${current_token}")
        else
            letta_token=$(ask "KITEZH_LETTA_TOKEN (leave blank for unauthenticated local server)" "")
        fi
        set_env "KITEZH_LETTA_TOKEN" "${letta_token}"
        ;;
    llamacpp)
        current_url=$(grep -E "^KITEZH_LLAMACPP_URL=" "${ENV_FILE}" | cut -d= -f2 || echo "http://localhost:8080")
        current_model=$(grep -E "^KITEZH_LLAMACPP_MODEL=" "${ENV_FILE}" | cut -d= -f2 || echo "nous-hermes-2-mixtral-8x7b-dpo-gguf")
        llamacpp_url=$(ask "KITEZH_LLAMACPP_URL" "${current_url}")
        llamacpp_model=$(ask "KITEZH_LLAMACPP_MODEL" "${current_model}")
        set_env "KITEZH_LLAMACPP_URL" "${llamacpp_url}"
        set_env "KITEZH_LLAMACPP_MODEL" "${llamacpp_model}"
        info "Start the llama-server first: $(bold "./scripts/llama_server_cpu.sh") (or _gpu.sh)"
        ;;
    *)
        warn "Unknown backend '${backend}' — you may need to configure it manually in .env."
        ;;
esac

echo ""

# ── Remote bridge ─────────────────────────────────────────────────────────────

if ask_yn "Enable the remote API bridge (KITEZH_REMOTE_ENABLED)?" "n"; then
    set_env "KITEZH_REMOTE_ENABLED" "1"
    current_url=$(grep -E "^KITEZH_REMOTE_URL=" "${ENV_FILE}" | cut -d= -f2 || echo "https://your-remote-backend.com")
    remote_url=$(ask "KITEZH_REMOTE_URL" "${current_url}")
    set_env "KITEZH_REMOTE_URL" "${remote_url}"
    ai_key=$(ask "KITEZH_AI_KEY")
    set_env "KITEZH_AI_KEY" "${ai_key}"
    signing_secret=$(ask "KITEZH_COMMAND_SIGNING_SECRET")
    set_env "KITEZH_COMMAND_SIGNING_SECRET" "${signing_secret}"
else
    set_env "KITEZH_REMOTE_ENABLED" "0"
    ok "Remote bridge disabled — web chat and CLI will use the local ${backend} backend."
fi

echo ""

# ── Web UI port ───────────────────────────────────────────────────────────────

current_port=$(grep -E "^KITEZH_WEB_PORT=" "${ENV_FILE}" | cut -d= -f2 || echo "7860")
web_port=$(ask "KITEZH_WEB_PORT (web chat interface port)" "${current_port}")
set_env "KITEZH_WEB_PORT" "${web_port}"

# ── LAN CIDR (admin route guard) ─────────────────────────────────────────────

current_cidr=$(grep -E "^KITEZH_LAN_CIDR=" "${ENV_FILE}" | cut -d= -f2 || echo "")
echo ""
echo "  Admin routes (/api/kai/*) are restricted to the LAN subnet when"
echo "  KITEZH_LAN_CIDR is set.  Leave blank to disable the restriction (dev mode)."
lan_cidr=$(ask "KITEZH_LAN_CIDR (e.g. 192.168.1.0/24, or blank)" "${current_cidr}")
set_env "KITEZH_LAN_CIDR" "${lan_cidr}"

# ── Headless display defaults ──────────────────────────────────────────────────

if (( HEADLESS_LINUX == 1 )); then
    current_video_driver=$(grep -E "^KITEZH_DISPLAY_VIDEO_DRIVER=" "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2 || echo "")
    if [[ -z "${current_video_driver}" ]]; then
        set_env "KITEZH_DISPLAY_VIDEO_DRIVER" "kmsdrm"
        ok "Headless Linux detected — set KITEZH_DISPLAY_VIDEO_DRIVER=kmsdrm for framebuffer face."
    else
        info "Headless Linux detected — keeping existing KITEZH_DISPLAY_VIDEO_DRIVER=${current_video_driver}."
    fi
fi

echo ""
ok "Configuration written to .env"
echo ""

# ── Step 5: Summary ───────────────────────────────────────────────────────────

echo "$(bold "[ 5/5 ]") Setup complete!"
echo ""
echo "  $(bold "To launch the web chat interface:")"
echo "    source .venv/bin/activate"
if [[ "${backend}" == "llamacpp" ]]; then
    echo "    ./scripts/llama_server_cpu.sh   # or _gpu.sh — start this first"
fi
echo "    python main.py --serve"
echo ""
echo "  $(bold "To start the CLI interactive loop:")"
echo "    source .venv/bin/activate"
echo "    python main.py"
echo ""
echo "  $(bold "To check connectivity:")"
echo "    python main.py --health"
echo ""
if (( HEADLESS_LINUX == 1 )); then
    echo "  $(bold "Headless display detected (no GUI):")"
    echo "    python main.py --framebuffer-face"
    echo ""
fi
if [[ "${backend}" == "letta" ]]; then
    echo "  $(bold "Letta note:") On first launch, Kitezh will auto-create a Letta agent"
    echo "  named '$(grep -E "^KITEZH_LETTA_AGENT_NAME=" "${ENV_FILE}" | cut -d= -f2 || echo "kai")' if one does not already exist."
    echo "  The resolved agent ID will be logged at startup."
    echo ""
fi
echo "  Happy hacking! 🤖"
echo ""
