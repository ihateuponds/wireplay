"""Game metadata from HowLongToBeat: completion times (Main / Main+Extra / 100%), a short synopsis, release year.

HowLongToBeat has no public API. This talks to the same two endpoints its own search page uses (checked
against the live site's JS bundle on 2026-10-10):
    GET  /api/search/site/init?t=<ms>   -> {"token": ...}   (needs a Referer header; the token is tied to IP + User-Agent)
    POST /api/search/site               header x-auth-token, JSON body -> {"data": [games ...]}
    GET  /game/<id>                     HTML with a __NEXT_DATA__ blob holding profile_summary (the synopsis)
Because that is not a documented contract, every failure is soft: callers get None ("no match") or a
covers.NetworkError ("try again later"), never an exception from parsing. Nothing here touches the UI or the DB
directly; fetch_row() is the one function that does both network and DB work and is meant for a worker thread.
"""
import difflib
import html
import json
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional

import covers
import db
from covers import NetworkError

BASE = "https://howlongtobeat.com"
USER_AGENT = "wireplay/1.0 (personal game launcher; results are cached locally)"
MIN_SCORE = 0.9                      # below this a search hit is not trusted to be the same game
TOKEN_TTL = 600.0                    # seconds a search token is reused
MAX_JSON_BYTES = 2 << 20
MAX_PAGE_BYTES = 3 << 20
SYNOPSIS_SENTENCES, SYNOPSIS_CHARS = 2, 320
# A sentence ends at . ! ? followed by a capital, except after common abbreviations ("Dr. Mario", "Mr. Driller").
_SENTENCE_END = (r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bJr\.)(?<!\bvs\.)(?<!\bNo\.)"
                 r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")

_token = {"value": None, "at": 0.0}
_token_lock = threading.Lock()


@dataclass(frozen=True)
class Metadata:
    main: Optional[float]            # hours
    extra: Optional[float]
    complete: Optional[float]
    synopsis: str
    year: str
    name: str = ""                   # HowLongToBeat's name for the match
    hltb_id: int = 0

    @property
    def has_data(self):
        return any((self.main, self.extra, self.complete, self.synopsis))


# ---------------------------------------------------------------- text helpers

def normalize_title(title: str) -> str:
    """Comparison form: accents folded, lower case, '&' = 'and', punctuation and the word 'the' dropped."""
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    text = text.replace("&", " and ").replace("'", "").replace("’", "")
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    return " ".join(w for w in words if w != "the")


def clean_title(title: str) -> str:
    """A row's own title without trailing [Hack, v1.2] style badges: what to look the game up by. (base_title is
    not enough: all 91 Pokémon versions share the base title 'Pokémon' but are 91 different games.)"""
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", title or "").strip() or (title or "")


def search_terms(title: str) -> list:
    """Words to search for: the title without punctuation ('Pokemon - Emerald Version' -> Pokemon Emerald Version),
    since a lone '-' or ':' in the terms makes HowLongToBeat find nothing."""
    return re.findall(r"[\w']+", title or "") or [title or ""]


def hours(seconds) -> Optional[float]:
    """HowLongToBeat reports seconds; the DB stores hours rounded to 0.1. 0 / missing -> None."""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    return round(value / 3600.0, 1) if value > 0 else None


def fmt_hours(h) -> str:
    """'~23h', '~4.5h', or '' when unknown."""
    if not h:
        return ""
    return f"~{round(h)}h" if h >= 10 else f"~{h:.1f}".rstrip("0").rstrip(".") + "h"


def short_synopsis(text, sentences=SYNOPSIS_SENTENCES, limit=SYNOPSIS_CHARS) -> str:
    """Plain text, first `sentences` sentences, at most `limit` characters (cut at a word with an ellipsis)."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(text or "")))
    text = " ".join(text.split())
    if not text:
        return ""
    parts = re.split(_SENTENCE_END, text)
    out = " ".join(parts[:sentences]).strip()
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"
    return out


# ---------------------------------------------------------------- HTTP

def _headers(extra=None):
    return {"User-Agent": USER_AGENT, "Referer": BASE + "/", "Accept": "*/*", **(extra or {})}


def _get_token(force=False) -> str:
    with _token_lock:
        if not force and _token["value"] and time.monotonic() - _token["at"] < TOKEN_TTL:
            return _token["value"]
        body = covers.http_get(f"{BASE}/api/search/site/init?t={int(time.time() * 1000)}", headers=_headers(),
                               max_bytes=MAX_JSON_BYTES, timeout=15)
        try:
            token = json.loads(body or b"{}").get("token")
        except (ValueError, AttributeError):
            token = None
        if not isinstance(token, str) or not token:
            raise NetworkError("HowLongToBeat gave no search token")
        _token.update(value=token, at=time.monotonic())
        return token


def _search_payload(title: str) -> bytes:
    return json.dumps({
        "searchType": "games", "searchTerms": search_terms(title), "searchPage": 1, "size": 20,
        "searchOptions": {
            "games": {"userId": 0, "platform": "", "sortCategory": "popular", "rangeCategory": "main",
                      "rangeTime": {"min": None, "max": None},
                      "gameplay": {"perspective": "", "flow": "", "genre": "", "difficulty": ""},
                      "rangeYear": {"min": "", "max": ""}, "modifier": ""},
            "users": {"sortCategory": "postcount"}, "lists": {"sortCategory": "follows"},
        },
        "filter": "", "sort": 0, "randomizer": 0, "useCache": True,
    }).encode()


def search(title: str) -> list:
    """Raw HowLongToBeat results for a title ([] when none). Refreshes the token once if it was rejected."""
    payload = _search_payload(title)
    for attempt in (0, 1):
        token = _get_token(force=attempt == 1)
        try:
            body = covers.http_get(f"{BASE}/api/search/site", timeout=20, max_bytes=MAX_JSON_BYTES, data=payload,
                                   headers=_headers({"Content-Type": "application/json", "x-auth-token": token}))
        except NetworkError as e:
            if e.status in (401, 403) and attempt == 0:
                continue                                # expired token: fetch a new one and retry once
            raise
        try:
            data = json.loads(body or b"{}").get("data")
        except (ValueError, AttributeError):
            raise NetworkError("HowLongToBeat returned something that is not JSON") from None
        return data if isinstance(data, list) else []
    return []


def best_match(title: str, results: list):
    """The result that is the same game as `title`, or None. Exact normalized name (or alias) wins; otherwise
    the closest name at or above MIN_SCORE. Ties go to the more played entry (more completions reported)."""
    want = normalize_title(title)
    if not want:
        return None
    best, best_key = None, None
    for game in results:
        if not isinstance(game, dict):
            continue
        names = [game.get("game_name") or ""] + [a.strip() for a in str(game.get("game_alias") or "").split(",")]
        score = max((difflib.SequenceMatcher(None, want, normalize_title(n)).ratio() for n in names if n), default=0.0)
        if score < MIN_SCORE:
            continue
        key = (score, game.get("comp_all_count") or game.get("count_comp") or 0)
        if best_key is None or key > best_key:
            best, best_key = game, key
    return best


def _synopsis(game_id) -> str:
    """profile_summary from the game's page, '' when unavailable."""
    page = covers.http_get(f"{BASE}/game/{int(game_id)}", headers=_headers(), timeout=20, max_bytes=MAX_PAGE_BYTES)
    if not page:
        return ""
    m = re.search(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
        game = data["props"]["pageProps"]["game"]["data"]["game"][0]
        return short_synopsis(game.get("profile_summary"))
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def fetch(title: str) -> Optional[Metadata]:
    """Look a game up by (base) title. None = HowLongToBeat has no confident match; NetworkError = try later."""
    results = search(title)
    game = best_match(title, results)
    if game is None:
        return None
    try:
        summary = _synopsis(game["game_id"])
    except NetworkError:
        summary = ""                                    # times are worth keeping even if the page failed
    year = game.get("release_world")
    return Metadata(
        main=hours(game.get("comp_main")), extra=hours(game.get("comp_plus")), complete=hours(game.get("comp_100")),
        synopsis=summary, year=str(year) if year else "", name=game.get("game_name") or "",
        hltb_id=int(game.get("game_id") or 0),
    )


# ---------------------------------------------------------------- per-row lookup + permanent cache

def fetch_row(game_id: int) -> Optional[Metadata]:
    """Fill the cache for one game row (and its stacked siblings that are the same game). Worker thread only.

    The lookup title is the row's own title (clean_title), so the 91 Pokémon versions are looked up one by one.
    Order: a row with the same title elsewhere (another platform) that already has data (no network) -> HowLongToBeat.
    A confident match is stored for good on every row of the stack that is the same game; "no match" is
    remembered (retried after db.META_RETRY_SECS); a network failure stores nothing so the next view retries.
    Returns what is now known, or None.
    """
    with db.session() as con:
        row = db.get_game(con, game_id)
        if row is None:
            return None
        title = clean_title(row["title"])
        siblings = [r["id"] for r in con.execute("SELECT id, title FROM games WHERE platform = ? AND base_title = ? COLLATE NOCASE",
                                                 (row["platform"], row["base_title"])) if clean_title(r["title"]).lower() == title.lower()]
        siblings = siblings or [game_id]
        twin = db.find_cached_metadata(con, row["title"], exclude_id=game_id)
        if twin is not None:
            meta = Metadata(twin["hltb_main"], twin["hltb_extra"], twin["hltb_complete"], twin["synopsis"] or "",
                            twin["release_year"] or "")
            for i in siblings:
                db.update_game_metadata(con, i, meta.main, meta.extra, meta.complete, meta.synopsis, meta.year)
            return meta
    meta = fetch(title)                                 # network, no DB connection held while waiting
    with db.session() as con:
        if meta is None or not meta.has_data:
            db.mark_metadata_checked(con, siblings)
            return None
        for i in siblings:
            db.update_game_metadata(con, i, meta.main, meta.extra, meta.complete, meta.synopsis, meta.year)
    return meta
