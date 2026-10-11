import support
support.fresh_db()
import os, struct, json
from pathlib import Path
from support import ok
import config, scanners

def vdf(entries):
    """A minimal binary shortcuts.vdf: {"shortcuts": {"0": {"appid": int, "appname": str}, ...}}"""
    b = b"\x00shortcuts\x00"
    for i, (aid, name) in enumerate(entries):
        b += b"\x00" + str(i).encode() + b"\x00" + b"\x02appid\x00" + struct.pack("<I", aid) + b"\x01appname\x00" + name.encode() + b"\x00\x08"
    return b + b"\x08\x08"

steam = support.SCRATCH / "steam"
(steam / "steamapps").mkdir(parents=True)
(steam / "userdata/12345/config").mkdir(parents=True)
(steam / "userdata/12345/config/shortcuts.vdf").write_bytes(vdf([(1001, "My Server Game"), (1002, "My Server Game Helper"), (1003, "Plain Game"), (1004, "Another One")]))
config.update(steam_dir=str(steam))
by = lambda: {g.title: g.launch_cmd for g in scanners.scan_steam_shortcuts()}

base = by()
ok(set(base) == {"My Server Game", "My Server Game Helper", "Plain Game", "Another One"}, f"without rules every shortcut is listed: {sorted(base)}")
ok(all(c.startswith("steam steam://rungameid/") for c in base.values()), "…and launched through Steam")
config.update(shortcut_overrides=[{"match": r"\bmy server game\b", "exclude": "helper", "command": "/opt/me/start-server.sh --fast"}])
got = by()
ok(got["My Server Game"] == "/opt/me/start-server.sh --fast", f"a matching shortcut gets the configured command: {got['My Server Game']!r}")
ok("My Server Game Helper" not in got, "…and one that also matches 'exclude' is hidden")
ok(got["Plain Game"] == base["Plain Game"] and got["Another One"] == base["Another One"], "other shortcuts are untouched")
config.update(shortcut_overrides=[{"match": "plain", "command": "~/bin/plain.sh"}])
ok(by()["Plain Game"] == os.path.expanduser("~/bin/plain.sh"), "'~' in a command is expanded")
config.update(shortcut_overrides=[{"match": "(unclosed", "command": "x"}, {"match": "another", "command": "ok.sh"}])
got = by(); ok(got["Another One"] == "ok.sh" and got["Plain Game"] == base["Plain Game"], "an invalid regex is ignored; later rules still apply")
for bad in ("text", 5, {"match": "plain"}, [None, 3, "x", {"command": "y"}, {"match": 5, "command": "z"}]):
    config.update(shortcut_overrides=bad)
    ok(by() == base, f"malformed shortcut_overrides ({str(bad)[:30]}) changes nothing")
config.update(shortcut_overrides=[{"match": "plain", "exclude": "game", "command": "never"}]); ok("Plain Game" not in by(), "exclude without a command still hides")
config.update(shortcut_overrides=[{"match": "plain", "command": "   "}]); ok(by()["Plain Game"] == base["Plain Game"], "a blank command does nothing")
print("ALL PASS")
