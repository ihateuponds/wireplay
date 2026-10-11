"""Local stand-ins for HowLongToBeat and RetroAchievements (127.0.0.1 only), with switchable failure modes."""
import json, struct, threading, time, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

API_KEY = "FAKE-RA-KEY-0123456789abcdef"      # not a real key
HASHES = {}                                    # ra game id -> [md5, ...] served by GetGameList?h=1


def png_bytes(w=2, h=2):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xe0\x31\x31" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


HLTB_GAMES = [
    {"game_id": 62941, "game_name": "Hades", "game_alias": "", "comp_main": 84937, "comp_plus": 174967, "comp_100": 343080,
     "release_world": 2020, "comp_all_count": 9000},
    {"game_id": 118218, "game_name": "Hades II", "game_alias": "", "comp_main": 115016, "comp_plus": 191855, "comp_100": 377417,
     "release_world": 2025, "comp_all_count": 3000},
    {"game_id": 5, "game_name": "Pokemon Emerald Version", "game_alias": "Pokemon Emerald", "comp_main": 135000, "comp_plus": 230000,
     "comp_100": 600000, "release_world": 2004, "comp_all_count": 100},
    {"game_id": 7, "game_name": "Portal 2", "game_alias": "", "comp_main": 29000, "comp_plus": 40000, "comp_100": 90000,
     "release_world": 2011, "comp_all_count": 100},
    {"game_id": 8, "game_name": "No Times Game", "game_alias": "", "comp_main": 0, "comp_plus": 0, "comp_100": 0,
     "release_world": 0, "comp_all_count": 1},
]
SUMMARY = ("Defy the god of death as you hack and slash your way out of the Underworld. Fight through "
           "&quot;Hades&quot; &amp; beyond in <b>this</b> rogue-like. Third sentence here. Fourth sentence there.")


class Fake:
    def __init__(self):
        self.log = []                      # (method, path, headers dict)
        self.tokens, self.token_n = set(), 0
        self.search_403_once = False
        self.search_status = None          # force this status on /api/search/site
        self.page_status = None            # force this status on /game/<id>
        self.ra_status = None              # force this status on every RA API call
        self.ra_error_json = None          # answer 200 with {"Error": ...}
        self.page_size = 2                 # completion-progress page size (forces paging)
        self.delay = 0.0                   # seconds every request takes (to prove the UI never waits on the network)
        self.gba_wrong_id = False
        self.lock = threading.Lock()
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *a): pass

            def _send(self, status, body=b"", ctype="application/json", extra=None):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _record(self):
                with fake.lock:
                    fake.log.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))

            def do_GET(self):
                self._record()
                time.sleep(fake.delay)
                u = urlsplit(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/api/search/site/init":
                    if not self.headers.get("Referer", ""):
                        return self._send(403, {"error": "Access Denied"})
                    with fake.lock:
                        fake.token_n += 1; tok = f"tok-{fake.token_n}"; fake.tokens.add(tok)
                    return self._send(200, {"token": tok})
                if u.path.startswith("/game/"):
                    if fake.page_status:
                        return self._send(fake.page_status, b"x", "text/html")
                    page = ('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps(
                        {"props": {"pageProps": {"game": {"data": {"game": [{"profile_summary": "" if u.path.endswith("/8") else SUMMARY}]}}}}}) + "</script></html>")
                    return self._send(200, page.encode(), "text/html")
                if u.path.startswith("/Badge/"):
                    name = u.path.rsplit("/", 1)[1]
                    if name.startswith("bad"):
                        return self._send(200, b"<html>not a png</html>", "image/png")
                    if name.startswith("gone"):
                        return self._send(404, b"nope", "text/plain")
                    return self._send(200, png_bytes(), "image/png")
                if u.path.startswith("/API/"):
                    return self._ra(u.path.rsplit("/", 1)[1], q)
                self._send(404, b"?", "text/plain")

            def do_POST(self):
                self._record()
                time.sleep(fake.delay)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if urlsplit(self.path).path != "/api/search/site":
                    return self._send(404, b"?", "text/plain")
                if fake.search_status:
                    return self._send(fake.search_status, {"error": "forced"}, extra={"Retry-After": "0"})
                tok = self.headers.get("x-auth-token", "")
                if tok not in fake.tokens or fake.search_403_once:
                    fake.search_403_once = False
                    return self._send(403, {"error": "Access Denied"})
                terms = " ".join(body.get("searchTerms", [])).lower()
                hits = [g for g in HLTB_GAMES if any(w in g["game_name"].lower() or w in g["game_alias"].lower() for w in terms.split())]
                self._send(200, {"count": len(hits), "data": hits})

            def _ra(self, endpoint, q):
                if fake.ra_status:
                    return self._send(fake.ra_status, {"message": "forced"}, extra={"Retry-After": "0"})
                if q.get("y") != API_KEY:
                    return self._send(401, {"message": "Invalid API Key"})
                if fake.ra_error_json:
                    return self._send(200, {"Error": fake.ra_error_json})
                if endpoint == "API_GetConsoleIDs.php":
                    gba = 99 if fake.gba_wrong_id else 5
                    return self._send(200, [
                        {"ID": 1, "Name": "Mega Drive"}, {"ID": 2, "Name": "Nintendo 64"}, {"ID": 3, "Name": "SNES/Super Famicom"},
                        {"ID": 4, "Name": "Game Boy"}, {"ID": gba, "Name": "Game Boy Advance"}, {"ID": 6, "Name": "Game Boy Color"},
                        {"ID": 7, "Name": "NES/Famicom"}, {"ID": 12, "Name": "PlayStation"}, {"ID": 16, "Name": "GameCube"},
                        {"ID": 18, "Name": "Nintendo DS"}, {"ID": 19, "Name": "Wii"}, {"ID": 21, "Name": "PlayStation 2"},
                        {"ID": 41, "Name": "PlayStation Portable"}] + ([{"ID": 5, "Name": "Something Else"}] if fake.gba_wrong_id else []))
                if endpoint == "API_GetGameList.php":
                    cid = int(q["i"])
                    games = {
                        3: [{"ID": 100, "Title": "EarthBound", "NumAchievements": 50, "ConsoleID": 3},
                            {"ID": 101, "Title": "~Hack~ EarthBound: Halloween", "NumAchievements": 12},
                            {"ID": 102, "Title": "Legend of Zelda, The: A Link to the Past", "NumAchievements": 70},
                            {"ID": 103, "Title": "No Achievements Yet", "NumAchievements": 0}],
                        5: [{"ID": 200, "Title": "Pokémon Emerald Version", "NumAchievements": 63},
                            {"ID": 201, "Title": "~Hack~ Pokemon Modern Emerald", "NumAchievements": 40}],
                        99: [{"ID": 200, "Title": "Pokémon Emerald Version", "NumAchievements": 63}],
                    }.get(cid, [])
                    if q.get("h") == "1":
                        games = [{**g, "Hashes": HASHES.get(g["ID"], [])} for g in games]
                    return self._send(200, games)
                if endpoint == "API_GetUserCompletionProgress.php":
                    allr = [
                        {"GameID": 100, "MaxPossible": 50, "NumAwarded": 50, "NumAwardedHardcore": 50, "HighestAwardKind": "mastered"},
                        {"GameID": 200, "MaxPossible": 63, "NumAwarded": 20, "NumAwardedHardcore": 18, "HighestAwardKind": ""},
                        {"GameID": 999, "MaxPossible": 10, "NumAwarded": 1, "NumAwardedHardcore": 1, "HighestAwardKind": ""},
                    ]
                    o, c = int(q.get("o", 0)), min(int(q.get("c", 100)), fake.page_size)
                    return self._send(200, {"Count": len(allr[o:o + c]), "Total": len(allr), "Results": allr[o:o + c]})
                if endpoint == "API_GetGameInfoAndUserProgress.php":
                    g = int(q["g"])
                    if g == 4040:
                        return self._send(200, {"ID": 4040, "Title": "Empty", "NumAchievements": 0, "Achievements": []})
                    if g not in (100, 200, 201):
                        return self._send(200, {})
                    ach = {}
                    for i in range(1, 7 if g in (200, 201) else 4):
                        a = {"ID": g * 10 + i, "Title": f"Achievement {i}", "Description": f"Do thing number {i} in the game.",
                             "Points": i * 5, "BadgeName": f"{g}{i:02d}", "DisplayOrder": 7 - i}
                        if i <= 2 or g == 100:
                            a["DateEarned"] = "2026-01-02 03:04:05"
                            if i == 1:
                                a["DateEarnedHardcore"] = "2026-01-02 03:04:05"
                        ach[str(a["ID"])] = a
                    return self._send(200, {"ID": g, "Title": "Fake Game", "NumAchievements": len(ach), "Achievements": ach,
                                            "NumAwardedToUser": sum(1 for a in ach.values() if a.get("DateEarned")), "NumAwardedToUserHardcore": 1})
                self._send(404, b"?", "text/plain")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def count(self, needle):
        return sum(1 for _m, path, _h in self.log if needle in path)

    def stop(self):
        self.server.shutdown()
