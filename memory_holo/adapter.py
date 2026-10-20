"""
Adapter — XYRIS I's real memory → the HOLO tree/stats payload shapes.

Pure functions, read-only: they call ``memory.memory_manager`` and return
plain dicts the derived page understands.  Design rules (Stage A §G):

  * every displayed field comes straight from the store — nothing is
    synthesized, no timestamps are invented (facts carry the store's
    YYYY-MM-DD ``updated``; sessions carry their real saved date/time);
  * empty categories are still served, as honest "0 entries" orbs;
  * per-orb caps are *stated* in the folder label ("30 of 139 on disk"),
    never a silent slice;
  * holo's markdown limits (18/14/48/420/4000) are kept only where they
    protect the layout (title/body/full clipping), not as data caps.

No Qt, no server, no network — unit-testable on its own.
"""
from __future__ import annotations

from memory import memory_manager as mm

# Presentation order mirrors the store's canonical category order; SESSIONS
# (transcripts from memory/sessions/*.json) is appended as the last orb.
CATEGORY_ORDER = ("identity", "preferences", "projects", "relationships",
                  "wishes", "notes")
SESSIONS_NAME = "SESSIONS"

# Layout guards, not storage limits (entries themselves are ≤380 chars).
TITLE_MAX = 48
BODY_MAX = 420
FULL_MAX = 4000
MAX_FILES_PER_ORB = 60
MAX_SESSION_CARDS = 30


def _pretty(key: str) -> str:
    return str(key).replace("_", " ").strip()


def store_fingerprint() -> str:
    """Cheap change-detector for the view server's response cache.

    One os.stat each on long_term.json (mtime+size — remember() rewrites the
    file) and sessions/ (dir mtime — transcripts are always new timestamped
    files, and deletes change the dir too).  The path is part of the key so
    two stores (app vs test tempdirs) can never share a cache entry."""
    parts = [str(mm.MEMORY_PATH)]
    try:
        s = mm.MEMORY_PATH.stat()
        parts += [str(s.st_mtime_ns), str(s.st_size)]
    except OSError:
        parts.append("absent")
    parts.append(str(mm._SESSIONS_DIR))
    try:
        parts.append(str(mm._SESSIONS_DIR.stat().st_mtime_ns))
    except OSError:
        parts.append("absent")
    return "|".join(parts)


def _clip(text, limit: int) -> str:
    text = str(text if text is not None else "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def _entries_label(shown: int, total: int) -> str:
    if shown >= total:
        return f"{total} entr{'y' if total == 1 else 'ies'}"
    return f"{shown} of {total} entries"


def _entry_card(category: str, row: dict) -> dict:
    key = str(row.get("key") or "")
    value = str(row.get("value") or "")
    updated = str(row.get("updated") or "")
    meta = f"{category} · updated {updated or 'unknown date'}"
    return {
        "name": key,
        "title": _clip(_pretty(key), TITLE_MAX),
        "body": _clip(value, BODY_MAX),
        "full": _clip(f"{value}\n\n— {meta}", FULL_MAX),
        "updated": updated,
    }


def _sessions_folder(limit: int) -> dict:
    sessions = mm.get_saved_sessions(limit=limit)
    total = mm.get_session_count()
    files = []
    for s in sessions:
        date = str(s.get("date") or "")
        time_ = str(s.get("time") or "")
        summary = str(s.get("summary") or "").strip()
        language = str(s.get("language") or "")
        turns = s.get("turns") or []
        if not isinstance(turns, list):
            turns = []
        try:
            turn_count = int(s.get("turn_count") or len(turns))
        except (TypeError, ValueError):
            turn_count = len(turns)

        title = _clip(f"{date} {time_}".strip()
                      or str(s.get("_file") or "session"), TITLE_MAX)
        body = summary or "(no summary saved for this session)"
        meta_bits = [b for b in (date, time_, f"{turn_count} turns",
                                 language) if b]
        full = f"{body}\n\n— {' · '.join(meta_bits) if meta_bits else 'session transcript'}"
        kept = 0
        for t in turns:
            line = f"\n{_clip(t, 300)}"
            if len(full) + len(line) > FULL_MAX - 60:
                break
            full += line
            kept += 1
        if kept < turn_count:
            full += f"\n… (+{turn_count - kept} more turns)"

        files.append({
            "name": str(s.get("_file") or title),
            "title": title,
            "body": _clip(body, BODY_MAX),
            "full": _clip(full, FULL_MAX),
            "updated": date,
        })

    if total > len(sessions):
        label = f"{len(sessions)} of {total} on disk"
    else:
        label = f"{total} session{'s' if total != 1 else ''}"
    return {"kind": "folder", "name": SESSIONS_NAME, "label": label,
            "files": files}


def build_tree(max_files: int = MAX_FILES_PER_ORB,
               max_sessions: int = MAX_SESSION_CARDS) -> list[dict]:
    """The HOLO /api/tree payload built from the real store.

    Seven orbs: the six memory categories (always present, honestly empty
    when empty) plus SESSIONS.  Rows arrive newest-first from
    ``all_entries_for_ui()`` and keep that order.
    """
    rows = mm.all_entries_for_ui()
    by_cat: dict[str, list[dict]] = {c: [] for c in CATEGORY_ORDER}
    for r in rows:
        cat = r.get("category")
        if cat in by_cat:
            by_cat[cat].append(r)

    tree = []
    for cat in CATEGORY_ORDER:
        items = by_cat[cat]
        shown = items[:max(0, int(max_files))]
        tree.append({
            "kind": "folder",
            "name": cat.upper(),
            "label": _entries_label(len(shown), len(items)),
            "files": [_entry_card(cat, r) for r in shown],
        })
    tree.append(_sessions_folder(max(0, int(max_sessions))))
    return tree


def build_stats() -> dict:
    """The HOLO /api/stats payload — real counts for the status line/ring."""
    stats = mm.get_memory_stats()
    stats["transcripts"] = mm.get_session_count()
    return stats
