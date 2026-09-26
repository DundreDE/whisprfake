#!/bin/bash
# whisprfake installer for Arch Linux / Omarchy (Hyprland + omarchy-shell). Idempotent: safe to re-run.
#   ./packaging/install.sh            core install (Parakeet + Qwen3 4B)
#   ./packaging/install.sh --all      also Whisper large-v3-turbo and Qwen3-ASR as alternative engines
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/whisprfake"
MODELS="$DATA/models"
SRC="$HOME/.local/opt/src"
WHISPER_TAG="v1.9.4"
ORT_VERSION="1.28.2"
ALL=0
PACKAGED=0  # installed via the Arch package: app + whisper.cpp live in /opt/whisprfake already
for a in "$@"; do
  case $a in --all) ALL=1 ;; --packaged) PACKAGED=1 ;; esac
done

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fetch() { # url dest
  [[ -s $2 ]] && return 0
  echo "   ↓ $(basename "$2")"
  curl -fL --retry 3 -o "$2.part" "$1" && mv "$2.part" "$2"
}

say "System-Pakete"
PKGS=(base-devel cmake git curl vulkan-headers spirv-headers shaderc vulkan-icd-loader gtk4 libadwaita
      gtk4-layer-shell python python-gobject at-spi2-core portaudio wl-clipboard wtype libnotify pipewire
      pipewire-pulse libpulse ollama uv)
if lspci | grep -qiE 'vga.*(amd|ati)|3d.*(amd|ati)'; then
  PKGS+=(vulkan-radeon)
  pacman -Qq ollama-rocm &>/dev/null || PKGS+=(ollama-rocm)
elif lspci | grep -qi 'nvidia'; then
  PKGS+=(ollama-cuda)
fi
sudo pacman -S --needed --noconfirm "${PKGS[@]}"
systemctl is-enabled ollama &>/dev/null || sudo systemctl enable --now ollama

say "Gruppe 'input' (für Ctrl+Super als Hotkey)"
if ! id -nG "$USER" | grep -qw input; then
  sudo gpasswd -a "$USER" input
  NEED_RELOGIN=1
fi

if (( ! PACKAGED )); then
  say "Python-Umgebung"
  cd "$REPO"
  [[ -d .venv ]] || uv venv --python /usr/bin/python3 --system-site-packages
  uv sync
  mkdir -p "$HOME/.local/bin"
  ln -sf "$REPO/.venv/bin/whisprfake" "$HOME/.local/bin/whisprfake"
fi

say "whisper.cpp $WHISPER_TAG mit Vulkan (Parakeet + Whisper auf der GPU)"
mkdir -p "$SRC"
if (( ! PACKAGED )) && [[ ! -x $SRC/whisper.cpp/build/bin/whisper-server || ! -f $SRC/whisper.cpp/build/bin/libparakeet.so ]]; then
  [[ -d $SRC/whisper.cpp ]] || git clone --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp "$SRC/whisper.cpp"
  cmake -S "$SRC/whisper.cpp" -B "$SRC/whisper.cpp/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build "$SRC/whisper.cpp/build" -j"$(nproc)"
fi
if (( ALL )) && [[ ! -x $SRC/llama.cpp/build/bin/llama-server ]]; then
  [[ -d $SRC/llama.cpp ]] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$SRC/llama.cpp"
  cmake -S "$SRC/llama.cpp" -B "$SRC/llama.cpp/build" -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF
  cmake --build "$SRC/llama.cpp/build" -j"$(nproc)" --target llama-server
fi

say "Modelle"
mkdir -p "$MODELS"
HF=https://huggingface.co
fetch "$HF/ggml-org/parakeet-GGUF/resolve/main/ggml-parakeet-tdt-0.6b-v3-f16.bin" "$MODELS/ggml-parakeet-tdt-0.6b-v3-f16.bin"
fetch "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx" "$MODELS/silero_vad.onnx"
fetch "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx" "$MODELS/3dspeaker_eres2net_base.onnx"
if [[ ! -f $MODELS/sherpa-onnx-pyannote-segmentation-3-0/model.onnx ]]; then
  fetch "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2" "$MODELS/seg.tar.bz2"
  tar xjf "$MODELS/seg.tar.bz2" -C "$MODELS" && rm "$MODELS/seg.tar.bz2"
fi
if [[ ! -f $MODELS/onnxruntime-linux-x64-$ORT_VERSION/lib/libonnxruntime.so.$ORT_VERSION ]]; then
  fetch "https://github.com/microsoft/onnxruntime/releases/download/v$ORT_VERSION/onnxruntime-linux-x64-$ORT_VERSION.tgz" "$MODELS/ort.tgz"
  tar xzf "$MODELS/ort.tgz" -C "$MODELS" && rm "$MODELS/ort.tgz"
fi
if (( ALL )); then
  fetch "$HF/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin" "$MODELS/ggml-large-v3-turbo.bin"
  fetch "$HF/ggml-org/Qwen3-ASR-1.7B-GGUF/resolve/main/Qwen3-ASR-1.7B-Q8_0.gguf" "$MODELS/Qwen3-ASR-1.7B-Q8_0.gguf"
  fetch "$HF/ggml-org/Qwen3-ASR-1.7B-GGUF/resolve/main/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf" "$MODELS/mmproj-Qwen3-ASR-1.7B-Q8_0.gguf"
fi
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull qwen3:8b

say "Dienst & Starter"
if (( ! PACKAGED )); then
  systemctl --user link "$REPO/packaging/whisprfake.service" 2>/dev/null || true
  mkdir -p "$HOME/.local/share/applications"
  cp "$REPO"/packaging/*.desktop "$HOME/.local/share/applications/"
fi
systemctl --user enable whisprfake.service

say "Hyprland (Fensterregeln, Super+Alt+W/N, Background switcher → Ctrl+Super+Shift+B)"
cp "$REPO/packaging/hypr/whisprfake.lua" "$HOME/.config/hypr/whisprfake.lua"
if ! grep -q 'hypr.whisprfake' "$HOME/.config/hypr/hyprland.lua"; then
  printf '\n-- whisprfake (local dictation)\npcall(require, "hypr.whisprfake")\n' >> "$HOME/.config/hypr/hyprland.lua"
fi
hyprctl reload >/dev/null 2>&1 || true

say "omarchy-shell Plugin (Flow Bar, Leisten-Symbol, Antwort-Popup) & Menü"
PLUG="$HOME/.config/omarchy/plugins/jakob.whisprfake"
mkdir -p "$PLUG"
cp "$REPO"/shell-plugin/jakob.whisprfake/* "$PLUG/"
omarchy plugin validate "$PLUG"
"$REPO/.venv/bin/python" "$REPO/packaging/omarchy_setup.py"
omarchy-restart-shell >/dev/null 2>&1 || true

say "Fertig"
if [[ ${NEED_RELOGIN:-0} == 1 ]]; then
  echo "Bitte einmal ab- und wieder anmelden (Gruppe 'input'). Danach startet whisprfake automatisch."
else
  systemctl --user restart whisprfake.service
  echo "whisprfake läuft. Ctrl+Super halten und sprechen. Hub: Super+Alt+W"
fi
