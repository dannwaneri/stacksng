#!/usr/bin/env bash
# Mux the narration track onto the silent testreel recording.
set -euo pipefail

FF="C:/Users/DELL/tools/testreel/node_modules/ffmpeg-static/ffmpeg.exe"
DIR="C:/Users/DELL/stacksng/demo_assets"

SILENT_VIDEO="${1:-$DIR/testreel-output/output.mp4}"
# v2 re-cut (Aug 3, 2026): scene timings in web/index.html were recomputed
# for narration_full_v2.wav (80.85s), not the original narration_full.wav
# (115.43s). Muxing the v1 file against v2-tuned scene timing is what
# caused "slides are faster than audio" — the visuals finish 35s before
# a 115s narration track does.
NARRATION="$DIR/audio/narration_full_v2.wav"
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
