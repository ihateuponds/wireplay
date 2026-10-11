# Contributing

Thanks for looking. This is a small personal project, so the process is light.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python3 tests/run_all.py
```

Run the tests before and after a change. They are headless, use a throw-away database/config/cache and never touch
the network, so they are safe to run on a machine with a real library.

## Ground rules

- **Never point a test or an experiment at your real library.** Use `tests/support.py` (it sets `WIREPLAY_TEST=1`) or the
  `WIREPLAY_DB` / `WIREPLAY_CONFIG` / `WIREPLAY_CACHE` environment variables.
- **Nothing personal in commits:** no API keys, no real paths, no screenshots of your own library (use
  `tools/screenshots.py`, which renders a fictional one).
- **Keep new network features optional and silent on failure.** wireplay must stay fully usable offline and with no keys.
- **A test for a bug fix:** add the failing case to the matching file in `tests/` first. Test files print `PASS` / `FAIL`
  lines through `support.ok()`; `tests/run_all.py` runs them all.
- Match the surrounding code style (small functions, docstrings that say *why*, no new dependencies unless they are worth it).
