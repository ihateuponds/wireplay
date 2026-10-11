import support
support.fresh_db()
import re, subprocess, sys
from support import ok
from version import __version__

root = support.PROJECT
log = (root / "CHANGELOG.md").read_text()
heads = re.findall(r"^## (\d+)\.(\d+)\.(\d+): .+ \((\d{4}-\d{2}-\d{2})\)$", log, re.M)
ok(re.fullmatch(r"\d+\.\d+\.\d+", __version__), f"version.py holds a plain x.y.z version: {__version__}")
ok(heads and ".".join(heads[0][:3]) == __version__, f"the newest CHANGELOG entry ({'.'.join(heads[0][:3]) if heads else None}) is the current version")
nums = [tuple(map(int, h[:3])) for h in heads]
ok(nums == sorted(nums, reverse=True) and len(set(nums)) == len(nums), f"{len(nums)} versions, listed newest first, none repeated")
dates = [h[3] for h in heads]
ok(dates == sorted(dates, reverse=True), "dates never go backwards as versions go up")
# the stated rule: a patch (0.x.y, y > 0) must follow a version with the same minor number
for (a, b, c), prev in zip(nums, nums[1:] + [None]):
    if c > 0:
        ok(prev is not None and prev[:2] == (a, b) and prev[2] == c - 1, f"{a}.{b}.{c} is a fix release that follows {a}.{b}.{c - 1}")
ok(all(n[0] == 0 for n in nums), "still pre-1.0")
for tool in ("app.py", "gui.py"):
    out = subprocess.run([sys.executable, str(root / tool), "--version"], capture_output=True, text=True, timeout=60)
    ok(out.returncode == 0 and out.stdout.strip() == f"wireplay {__version__}", f"{tool} --version prints 'wireplay {__version__}'")
ok(f"Version {__version__}" in (root / "README.md").read_text() or f"**Version {__version__}**" in (root / "README.md").read_text(), "the README states the current version")
print("ALL PASS")
