#!/usr/bin/env bash
# Download the qwen2.5-coder-7b Q4_K_M GGUF used by StacksNG.
#
# Rules (per ADTC submission spec):
#   - Idempotent: safe to run multiple times.
#   - No credentials: public URL only.
#   - Output path must match `_runtime.model_path` in metadata.json.
#
# The URL below is the lmstudio-community mirror on HuggingFace. Its Q4_K_M
# artefact is byte-identical to the Ollama-distributed qwen2.5-coder:7b blob
# that we used for the participant-mode profiler run (both are 4,683,074,048
# bytes), so audit reproducibility gets the exact same weights we benchmarked.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_DIR="$HERE/model"
MODEL_FILE="$MODEL_DIR/qwen2.5-coder-7b-Q4_K_M.gguf"

MODEL_URL="https://huggingface.co/lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"

mkdir -p "$MODEL_DIR"

if [[ -f "$MODEL_FILE" ]]; then
  echo "model already present at $MODEL_FILE — skipping download"
  exit 0
fi

echo "downloading $MODEL_URL"
echo "  -> $MODEL_FILE (~4.7 GB)"

if command -v curl > /dev/null 2>&1; then
  curl -L --fail --progress-bar -o "$MODEL_FILE.partial" "$MODEL_URL"
elif command -v wget > /dev/null 2>&1; then
  wget --show-progress -O "$MODEL_FILE.partial" "$MODEL_URL"
else
  echo "error: neither curl nor wget found on PATH" >&2
  exit 1
fi

mv "$MODEL_FILE.partial" "$MODEL_FILE"
echo "done: $MODEL_FILE"
