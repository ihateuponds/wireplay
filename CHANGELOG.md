# Changelog

Versions follow a simple rule while wireplay is pre-1.0: **a round of work that adds a feature raises the middle number
(0.x.0); a round that only fixes bugs raises the last (0.x.y).** Dates are local time (UTC-4). The early versions were
numbered retroactively from the project's history, so they have no git tags of their own; the first public commit is tagged
with the current version.

## 0.16.0: a real app, ready to publish (2026-10-10)
- `./install.sh` installs a menu entry, icon and `wireplay` command for the current user (`--uninstall` removes them).
- `shortcut_overrides` in `config.json` replaces a hard-coded special case for one non-Steam game with a documented,
  general feature: give any shortcut its own launch command, or hide it.
- A self-contained test suite in `tests/` (529 checks over a fictional library, no real data, no network) and
  `tools/screenshots.py`, which renders the README screenshots from a made-up demo library.
- README rewrite with screenshots, `CONTRIBUTING.md`, `wireplay --version`, this changelog.
- Fixed: the playtime line in the details drawer was cut off ("Last played: yeste…"). Tests could write to the real art cache.

## 0.15.0: open-source polish (2026-10-10)
- Library folders are config-driven with auto-detection: `roms_dir`, `media_dir`, `steam_dir`, `heroic_dir` (native, `~/.steam`
  and Flatpak Steam; native and Flatpak Heroic; EmuDeck, ES-DE, SD-card and `~/Emulation` layouts).
- **⚙️ Settings** dialog (`Ctrl+,`): library folders with Browse / Auto-Detect and a live check, API keys, Discord toggle, card
  width, startup tab. Changes apply without a restart.
- **First-run wizard** for new installs: welcome, detected platforms, optional keys, then the first scan.
- MIT license, `.gitignore`, `config.json.example`, desktop entry and icon.
- A missing launcher that was never installed no longer shows a "not found, skipped" warning on every scan.

## 0.14.0: exact trophies, offline art, big collections (2026-10-10)
- RetroAchievements **hash matching**, on demand when a ROM is opened or launched (never a bulk scan): headers are stripped as
  RetroAchievements does it (iNES, SNES copier, N64 byte order). The verdict is cached in the database.
- **Offline box art**: art read from the ROM drive is copied to the local cache and served from there when the drive is gone.
- **Collection browser** for stacks of more than 5 versions: search, kind chips (`#translation`, `#hack`, `#subset`, `#revision`),
  per-version playtime and trophies, double-click to launch.
- **Recently Played shelf** with one-click resume, and `Played Recently` / `Never Played` quick filters.
- **Session history** in the drawer: click the playtime to list recent sessions.
- Fixed: finishing an older session late could move "last played" backwards.

## 0.13.1: trophies on the right games (2026-10-10)
- Fixed: translations and hacks inherited the original game's RetroAchievements set (EarthBound's 27 versions all showed the
  same trophies). They now match only their own set; a plain `[Rev 1]` still uses the base game's. Stale matches are cleared.

## 0.13.0: thumbnail cache (2026-10-10)
- Cards now read a small cached thumbnail instead of re-decoding the 1 MB original every launch: 39 MB of reads became 1.7 MB and
  warm loads got about 32 times faster. Transparent covers are cached losslessly; changed art gets a fresh thumbnail.

## 0.12.0: deep game info (2026-10-10)
- **HowLongToBeat** times (main, main+extra, 100%), synopsis and release year, fetched on demand and cached permanently.
- **RetroAchievements** (optional keys): `🏆 X/Y` card badges, gold `🥇 Mastered`, progress and hardcore points in the drawer, an
  expandable achievement list with cached badge icons.
- **Syncthing awareness**: a `🔄 Syncthing (<device>)` badge and a button that opens the synced save folder.
- The details drawer is organised into sections: header, synopsis, HowLongToBeat, RetroAchievements, save sync and playtime, actions.
- Every network and filesystem lookup runs off the UI thread. Quitting no longer waits for slow lookups.

## 0.11.0: sessions, tags, Heroic playtime (2026-10-10)
- A `sessions` table with crash/restart recovery: games still running are picked up again, finished ones are credited to their
  last heartbeat.
- Playtime tracking for Epic games launched through Heroic.
- Custom **tags** with a `#tag` search, collection chips and a tag editor in the drawer.
- Test isolation: `WIREPLAY_TEST=1` makes the database and config refuse the real files.
- Fixed: "Session recorded" was always overwritten by the game count; a resumed emulator session could not be closed.

## 0.10.0: zoom, accurate Steam playtime, Discord (2026-10-10)
- Card zoom: `[-] 100% [+]`, `Ctrl+=` / `Ctrl+-` / `Ctrl+0`, Ctrl+wheel; the grid reflows live and keeps your place.
- Steam playtime from the real game process (found through `/proc`), so the time is right even though `steam -applaunch` exits at once.
- Discord Rich Presence over Discord's local socket, with no extra libraries.
- Fixed: launching an uninstalled game no longer counts as a play session.

## 0.9.0: Epic Games through Heroic (2026-10-10)
- Epic library scan, Epic CDN art with SteamGridDB fallback, launch through `heroic://`, an `INSTALL` badge for games not installed.

## 0.8.1: drawer crash (2026-10-10)
- Fixed a crash when opening and closing the details drawer quickly, plus the drawer's width limits, `Esc` priority
  (search, then drawer, then quit) and unsaved edits being flushed on close.

## 0.8.0: playtime, favorites, details drawer (2026-10-10)
- Playtime and last-played, a ★ Favorites tab (`F`), a sort button (A-Z / recent / playtime), and a 320 px details drawer with
  poster, stats, notes and custom launch arguments.

## 0.7.1: audit and hardening (2026-10-10)
- A full code audit: cleaner structure, thread-safe poster loading with bounded caches and decoding off the UI thread,
  resilience to corrupt binary VDF files, network timeouts and rate limits, safe database connection handling, properly
  detached game processes, and shutdown traps in the helper script.

## 0.7.0: SteamGridDB posters (2026-10-10)
- Clean 600x900 vertical posters from SteamGridDB (optional API key), and smart handling of Pokémon hacks with base-game fallbacks.

## 0.6.0: graphical launcher (2026-10-10)
- `gui.py`: a PySide6 launcher with the dark "WIRED" look, a card grid, vertical Steam posters, search, tabs and a cover picker.

## 0.5.1: cover art fixes (2026-10-09)
- Fixed distorted terminal art (proper 3:4 box shape, clean half-block pixels, centred) and added automatic fetching of missing
  retro covers from libretro thumbnails.

## 0.5.0: cover art (2026-10-09)
- Cover art in a two-column terminal layout: EmuDeck art for ROMs, Steam art cached locally, rendered with `chafa`.

## 0.4.1: grouping fix (2026-10-09)
- Fixed version grouping: matching is case-insensitive and hack subtitles are split off, so all 27 EarthBound ROMs fall into one group.

## 0.4.0: platform tabs and version stacking (2026-10-09)
- Platform tabs, and every game split into a base title plus a version label, stacked per system (never across platforms).

## 0.3.0: smarter titles (2026-10-09)
- Titles keep what matters (RetroAchievements subsets, hacks, editions, revisions, translations) as a `[badge]` and drop region tags.

## 0.2.0: retro platforms through RetroArch (2026-10-09)
- SNES, NES, N64, Genesis, Mega Drive, 32X and Atari 2600 games launch through Flatpak RetroArch cores instead of opening a browser.

## 0.1.1: AppImage emulators (2026-10-09)
- Fixed: AppImage emulators no longer trigger AppImageLauncher pop-ups.

## 0.1.0: foundation (2026-10-09)
- The first milestone: a SQLite library (`wireplay.db`), Steam and ROM scanners, and a curses terminal launcher.
