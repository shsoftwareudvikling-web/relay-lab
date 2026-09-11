"""Start RelayLab's loopback API and Vite console together."""
from __future__ import annotations
import subprocess
import shutil
import sys
from pathlib import Path

root = Path(__file__).parents[1]
pnpm = shutil.which("pnpm.cmd" if sys.platform == "win32" else "pnpm")
if not pnpm:
    raise SystemExit("pnpm was not found on PATH")
processes = [
    subprocess.Popen([sys.executable, str(root / "backend" / "server.py")], cwd=root),
    subprocess.Popen([pnpm, "exec", "vite", "--port", "5179", "--strictPort"], cwd=root),
]
try:
    raise SystemExit(processes[1].wait())
except KeyboardInterrupt:
    pass
finally:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
