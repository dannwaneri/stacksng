#!/usr/bin/env bash
# Mux the narration track onto the silent testreel recording.
set -euo pipefail

FF="C:/Users/DELL/tools/testreel/node_modules/ffmpeg-static/ffmpeg.exe"
DIR="C:/Users/DELL/stacksng/demo_assets"

SILENT_VIDEO="${1:-$DIR/testreel-output/output.mp4}"
NARRATION="$DIR/audio/narration_full.wav"
OUT="C:/Users/DELL/stacksng/demo.mp4"

echo "silent video: $SILENT_VIDEO"
echo "narration:    $NARRATION"
echo "output:       $OUT"

"$FF" -y \
  -i "$SILENT_VIDEO" \
  -i "$NARRATION" \
  -c:v copy -c:a aac -b:a 192k \
  -map 0:v:0 -map 1:a:0 \
  -shortest \
  "$OUT"

echo
echo "=== final duration check ==="
"$FF" -i "$OUT" 2>&1 | grep -E "Duration|Video:|Audio:"
