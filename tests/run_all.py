#!/usr/bin/env python3
"""Run every test_*.py in this folder, each in its own process, and summarise.

    python3 tests/run_all.py            # everything
    python3 tests/run_all.py config art # only files whose name contains one of the words

Tests use a throw-away database, config and cache (see support.py) and never touch your real library or the internet.
They need PySide6 for the GUI tests and run headless (QT_QPA_PLATFORM=offscreen), and a Linux system (/proc, bash).
"""
import re
import subprocess
import sys
import time
from pathlib import Path

here = Path(__file__).resolve().parent
words = sys.argv[1:]
files = sorted(p for p in here.glob("test_*.py") if not words or any(w in p.name for w in words))
failed, total = [], 0
for path in files:
    t = time.time()
    try:
        proc = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, timeout=600)
        out, code = proc.stdout + proc.stderr, proc.returncode
    except subprocess.TimeoutExpired as e:
        out, code = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""), 124
    passed = len(re.findall(r"^PASS", out, re.M))
    total += passed
    status = "ok  " if code == 0 and "ALL PASS" in out else "FAIL"
    print(f"{status} {path.name:24} {passed:4} checks  {time.time() - t:5.1f}s")
    if status == "FAIL":
        failed.append(path.name)
        print("     " + "\n     ".join([l for l in out.splitlines() if l.startswith("FAIL") or "Traceback" in l or l.strip().startswith("File")][:6]))
print(f"\n{total} checks passed in {len(files) - len(failed)}/{len(files)} files" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
sys.exit(1 if failed else 0)
