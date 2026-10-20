"""
Loopback server for the Living Memory Brain view (Stage A — Option 4).

Why stdlib: holo's own 180-line server proves ``BaseHTTPRequestHandler`` is
enough for one page and three endpoints, so the view adds **zero** new
dependencies (requirements.txt philosophy: heavy extras stay opt-in).

Why 127.0.0.1:4891: the remote dashboard owns 0.0.0.0:8000/8001 and stock
holo owns 4890 (Stage A §F).  Loopback alone keeps everything off the LAN —
no firewall prompts, no auth surface — and modern browsers still treat
http://127.0.0.1 as a secure context, so a future landmark/camera feed stays
unblocked.  Override with env ``XYRIS_MEMORY_VIEW_PORT``.

Endpoints
---------
  GET  /              memory_view.html (derived holo page, Cache-Control:
                      no-store so a fixed page is never a stale page)
  GET  /api/tree      adapter.build_tree()  — categories → orbs, entries → cards
  GET  /api/stats     adapter.build_stats() — real counts for the status line
  GET  /api/hands     latest landmark frame pushed by the XYRIS gesture engine
                      (Phase 4 — engine owns the camera; the page only polls).
                      Empty + age_ms=-1 when the engine is off or stalled.
  POST /api/state     board event → activity feed.  READ-ONLY by contract:
                      this endpoint records what the *board* did (open, dock,
                      dismiss — all visual) and never calls into the memory
                      store.  Per-frame "grab" noise is filtered out.

Fail-soft, twice over: a handler exception answers with a JSON error (the
page falls back to its honest "NO FEED" orb), and ``start()`` prints and
returns None on a busy port instead of taking the app down — the same
contract as dashboard/server.py's startup.

Speed (make-it-fast pass): responses ride HTTP/1.1 keep-alive with
TCP_NODELAY (two boot fetches share one connection; no Nagle stall on small
JSON), and /api/* payloads are served from a store-fingerprint cache — two
stat() calls decide staleness, so a warm hit never re-reads 30 transcripts.
The cache invalidates the instant the store changes (remember() rewrites
long_term.json → mtime/size; new/deleted transcripts → dir mtime), with a
short TTL as a backstop for edits made outside the app.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import adapter

HOST = "127.0.0.1"
DEFAULT_PORT = 4891                    # 4890 = holo, 8000/8001 = dashboard
PORT_ENV = "XYRIS_MEMORY_VIEW_PORT"
MAX_BODY = 64 * 1024

# Board events worth a row in the activity center.  holo also posts
# "grab"/"fling" style per-gesture noise; those stay out of the feed on
# purpose (Stage A §G — the feed is user-facing observability, not a trace).
STATE_KINDS = frozenset({"open", "open_folder", "close_folder", "dismiss",
                         "dock", "fling", "clear", "tidy", "sum"})

_page_cache: bytes | None = None
_feed = None                     # bound by main/ui (ActivityFeed-like .append)
_state_events = 0
_running: ThreadingHTTPServer | None = None

# Latest landmark frame from the XYRIS gesture engine (Phase 4).  The engine
# thread pushes; HTTP threads read the reference — one atomic swap, no lock.
_hands: dict = {"hands": [], "vw": 0, "vh": 0}
_hands_at: float = 0.0
HANDS_STALE_S = 1.0              # engine runs at ~30fps; >1s old = gone

# path → (built_at, fingerprint, serialized bytes).  Dict get/set are atomic
# under the GIL, so no lock: a rare duplicate build is harmless (idempotent).
_CACHE: dict[str, tuple[float, str, bytes]] = {}
_CACHE_TTL = 5.0       # rebuild backstop for edits that never touch an mtime
                       # (a transcript hand-edited in place); correctness of
                       # normal writes comes from the fingerprint, checked
                       # BEFORE the TTL so new data appears instantly.


def set_activity_feed(feed) -> None:
    """Bind the app's ActivityFeed (or None to unbind). Tests pass a stub."""
    global _feed
    _feed = feed


def state_event_count() -> int:
    """How many allowlisted board events were recorded since import."""
    return _state_events


def publish_hands(payload: dict | None) -> None:
    """Accept one landmark frame from the gesture engine (Phase 4 push side).

    payload: {"hands": [[{x,y}×21], …≤2], "vw": frame_w, "vh": frame_h} in
    MediaPipe normalized coordinates — exactly what the page's ingestHands()
    consumes.  Bounds are enforced here so a bad caller can't flood the page.
    None / garbage resets to an empty frame (hands cleanly drop, 280ms
    watchdog in the page agrees)."""
    global _hands, _hands_at
    hands = []
    if isinstance(payload, dict):
        for hand in (payload.get("hands") or [])[:2]:
            pts = []
            for pt in (hand or [])[:21]:
                try:
                    pts.append({"x": float(pt["x"]), "y": float(pt["y"])})
                except Exception:
                    pts = []
                    break
            if len(pts) == 21:
                hands.append(pts)
        try:
            vw, vh = int(payload.get("vw") or 0), int(payload.get("vh") or 0)
        except (TypeError, ValueError):
            vw, vh = 0, 0
    else:
        vw, vh = 0, 0
    _hands = {"hands": hands, "vw": vw, "vh": vh}
    _hands_at = time.monotonic()


def _hands_snapshot() -> dict:
    """What GET /api/hands serves: the frame if fresh, honest empties if not."""
    age = (time.monotonic() - _hands_at) if _hands_at else None
    if age is None or age > HANDS_STALE_S:
        return {"hands": [], "vw": _hands["vw"], "vh": _hands["vh"],
                "age_ms": -1 if age is None else int(age * 1000)}
    return {"hands": _hands["hands"], "vw": _hands["vw"], "vh": _hands["vh"],
            "age_ms": int(age * 1000)}


def _page() -> bytes:
    global _page_cache
    if _page_cache is None:
        try:
            _page_cache = Path(__file__).with_name("memory_view.html").read_bytes()
        except OSError:
            _page_cache = (b"<!doctype html><title>memory view</title>"
                           b"<p>memory_view.html is missing from the install.</p>")
    return _page_cache


def _cached_json(path: str, builder) -> bytes:
    """Pre-serialized payload; rebuilt when the store changed (2 stat()s)
    or the backstop TTL lapsed.  Warm hit ≈ free."""
    now = time.monotonic()
    fp = adapter.store_fingerprint()
    hit = _CACHE.get(path)
    if hit and hit[1] == fp and now - hit[0] < _CACHE_TTL:
        return hit[2]
    body = json.dumps(builder(), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    _CACHE[path] = (now, fp, body)
    return body


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that disables Nagle — every response here is a
    tiny JSON blob, and the 40ms worst-case delayed ACK is pure latency."""

    def get_request(self):
        sock, addr = super().get_request()
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        return sock, addr


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive: the page's fetches share
                                    # one connection instead of handshaking
                                    # twice per reload
    def log_message(self, *args):        # quiet, like holo's handler
        pass

    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        try:
            b = body if isinstance(body, bytes) else \
                json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if ctype.startswith("text/html"):
                self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass                          # client navigated away mid-write

    def do_GET(self):
        p = self.path.split("?")[0]
        try:
            if p in ("/", "/memory_view.html"):
                return self._send(200, _page(), "text/html; charset=utf-8")
            if p == "/api/tree":
                return self._send(200,
                                  _cached_json("/api/tree", adapter.build_tree))
            if p == "/api/stats":
                return self._send(200,
                                  _cached_json("/api/stats", adapter.build_stats))
            if p == "/api/hands":
                return self._send(200, _hands_snapshot())
        except Exception as e:            # one bad JSON read must not kill the loop
            return self._send(500, {"error": type(e).__name__})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/api/state":
            return self._send(404, {"error": "not found"})
        global _state_events
        try:
            declared = int(self.headers.get("Content-Length") or 0)
            n = min(declared, MAX_BODY)
            raw = self.rfile.read(n) if n > 0 else b""
            if declared > MAX_BODY:
                # Keep-alive: unread residue would corrupt the next request
                # on this connection — answer, then drop it.
                self.close_connection = True
            data = json.loads(raw or b"{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        kind = str(data.get("event") or "")[:40]
        card = str(data.get("card") or "")[:120]
        if kind in STATE_KINDS:
            _state_events += 1
            feed = _feed
            if feed is not None:
                try:
                    # core/activity.summarize() prints custom kinds verbatim
                    # (underscores → spaces, first letter capitalised), so
                    # folding the card into the kind is what makes the row
                    # read "Open folder — preferences" without touching core.
                    detail = f"{kind} — {card}" if card else kind
                    feed.append({
                        "event_type": "custom",
                        "source": "memory_view",
                        "data": {"kind": detail},
                        "timestamp": time.time(),
                    })
                except Exception:         # observability never breaks the view
                    pass
        self._send(200, {"ok": True})


def create_server(host: str = HOST, port: int = 0) -> ThreadingHTTPServer:
    """Build (but do not start) a server.  port=0 asks the OS for a free
    port — what the tests use so they never collide with a running app."""
    return _Server((host, port), _Handler)


def start(port: int | None = None) -> ThreadingHTTPServer | None:
    """Fail-soft startup: None (with one printed line) if the port is taken."""
    global _running
    if port is None:
        try:
            port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))
        except (TypeError, ValueError):
            port = DEFAULT_PORT
    try:
        srv = create_server(port=port)
    except OSError as e:
        print(f"[MemoryView] Disabled: {e}")
        return None
    t = threading.Thread(target=srv.serve_forever, daemon=True,
                         name="memory-view")
    t.start()
    _running = srv
    print(f"[MemoryView] http://{srv.server_address[0]}:"
          f"{srv.server_address[1]}/")
    return srv


def stop(srv: ThreadingHTTPServer | None) -> None:
    global _running
    if srv is None:
        return
    try:
        srv.shutdown()
        srv.server_close()
    except Exception:
        pass
    if _running is srv:
        _running = None


def get_url() -> str | None:
    """URL of the live view server, starting one on demand if the app boot
    path didn't (tests, late entry points).  None = server disabled."""
    global _running
    if _running is None:
        _running = start()
    if _running is None:
        return None
    host, port = _running.server_address[0], _running.server_address[1]
    return f"http://{host}:{port}/"


def open_in_browser() -> str | None:
    """Start the view if needed and open it in the system default browser.

    The one entry point both the Memory page button and the memory_controller
    'view' action call, so the port/URL decision lives in exactly one place.
    Returns the URL, or None when the server could not start (fail-soft —
    the caller reports, never raises)."""
    url = get_url()
    if not url:
        return None
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    return url
