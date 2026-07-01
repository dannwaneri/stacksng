"""Record a screencast demo of StacksNG answering four canned queries.

Design (differs from a naive pyautogui-typewriter approach):
  - We do NOT type into a terminal via pyautogui — that's fragile (window
    focus, key timing). Instead we generate a PowerShell script that runs
    all four queries sequentially and launch it in a new console window.
  - We do NOT poll the screen for "output stopped changing" — we control
    the PowerShell child process, so we simply wait for it to exit.
  - Screen capture uses `mss` (fast BitBlt) instead of `pyautogui.screenshot`
    which is ~10× slower and drops the frame rate below the target.
  - `pyautogui` is used only to park the mouse cursor off-screen before
    recording, so it doesn't appear in the frame.

Deps:
    pip install mss opencv-python numpy pyautogui

Usage:
    python scripts/demo_recorder.py
    # (a 3-second countdown gives you time to alt-tab away)

Output:
    C:\\Users\\DELL\\stacksng\\demo.mp4
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import cv2
import mss
import numpy as np
import pyautogui

PROJECT_ROOT = Path(r"C:\Users\DELL\stacksng")
OUTPUT_PATH = PROJECT_ROOT / "demo.mp4"
PS_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "_demo_run.ps1"

FPS = 10
PAUSE_BETWEEN_S = 5

QUERIES = [
    "How do I verify a Paystack webhook signature in Node.js?",
    "How do I initialize a Flutterwave payment in Python?",
    "What is the difference between NGN and kobo in Paystack?",
    "How do I send an OTP with Termii?",
]

# subprocess.CREATE_NEW_CONSOLE — spawn the PS window as its own OS console
CREATE_NEW_CONSOLE = 0x00000010


def write_ps_script() -> Path:
    """Write the PowerShell demo driver to a .ps1 file. Returns the path."""
    lines: list[str] = [
        f"Set-Location -Path '{PROJECT_ROOT}'",
        "Clear-Host",
        "Write-Host 'StacksNG demo - 4 queries' -ForegroundColor Cyan",
        "Write-Host 'Offline. On-device. Cited.' -ForegroundColor DarkGray",
        "Write-Host ''",
        "Start-Sleep -Seconds 2",
    ]
    for i, q in enumerate(QUERIES, 1):
        # PowerShell single-quoted string: escape a literal ' by doubling it.
        safe_q = q.replace("'", "''")
        lines += [
            "Write-Host ''",
            f"Write-Host '=== Query {i}/{len(QUERIES)} ===' -ForegroundColor Yellow",
            f"Write-Host 'PS> python scripts/query.py \"{q}\"' -ForegroundColor Gray",
            "Start-Sleep -Seconds 1",
            f"python scripts/query.py '{safe_q}'",
            f"Start-Sleep -Seconds {PAUSE_BETWEEN_S}",
        ]
    lines += [
        "Write-Host ''",
        "Write-Host 'Demo complete.' -ForegroundColor Green",
        "Start-Sleep -Seconds 3",
    ]
    PS_SCRIPT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PS_SCRIPT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return PS_SCRIPT_PATH


def recorder_loop(stop_event: threading.Event, output_path: Path) -> None:
    """Capture the primary display until stop_event is set. Encode to mp4."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # index 0 is "all displays combined"; 1 is primary
        width, height = monitor["width"], monitor["height"]
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(output_path), fourcc, FPS, (width, height))
        if not writer.isOpened():
            raise RuntimeError(
                f"cv2.VideoWriter failed to open {output_path}. "
                "Codec 'mp4v' may not be available in this OpenCV build."
            )
        frame_interval = 1.0 / FPS
        while not stop_event.is_set():
            t0 = time.time()
            raw = np.asarray(sct.grab(monitor))
            # mss returns BGRA; the video writer expects BGR
            frame = cv2.cvtColor(raw, cv2.COLOR_BGRA2BGR)
            writer.write(frame)
            elapsed = time.time() - t0
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)
        writer.release()


def launch_terminal(script_path: Path) -> subprocess.Popen:
    """Spawn a new PowerShell console running the demo script."""
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(script_path),
    ]
    return subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)


def park_mouse() -> None:
    """Move the cursor to a corner so it does not appear over content."""
    screen_w, screen_h = pyautogui.size()
    pyautogui.moveTo(screen_w - 1, screen_h - 1, duration=0)


def main() -> int:
    if not PROJECT_ROOT.exists():
        print(f"[demo_recorder] stacksng not found at {PROJECT_ROOT}", file=sys.stderr)
        return 1

    script_path = write_ps_script()
    print(f"[demo_recorder] wrote {script_path}")

    # Countdown so the operator can prep the desktop (close notifications, etc.)
    for i in range(3, 0, -1):
        print(f"[demo_recorder] starting in {i}...")
        time.sleep(1)

    park_mouse()

    stop_event = threading.Event()
    rec_thread = threading.Thread(
        target=recorder_loop, args=(stop_event, OUTPUT_PATH), daemon=True
    )
    rec_thread.start()

    # Give the writer a moment to open the file before the terminal window pops.
    time.sleep(0.5)

    try:
        proc = launch_terminal(script_path)
        print(f"[demo_recorder] terminal launched (pid={proc.pid}); recording...")
        proc.wait()
        # Let the closing "Demo complete." frame stay visible briefly.
        time.sleep(2)
    finally:
        stop_event.set()
        rec_thread.join(timeout=10)

    if OUTPUT_PATH.exists():
        size_mb = OUTPUT_PATH.stat().st_size / (1024 * 1024)
        print(f"[demo_recorder] saved: {OUTPUT_PATH}  ({size_mb:.1f} MB)")
    else:
        print(f"[demo_recorder] WARNING: no output at {OUTPUT_PATH}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
