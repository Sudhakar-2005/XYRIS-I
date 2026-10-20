import json
import os
import re
from datetime import datetime
from threading import Lock
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR         = get_base_dir()
MEMORY_PATH      = BASE_DIR / "memory" / "long_term.json"
_lock            = Lock()
MAX_VALUE_LENGTH = 380

# ── Why there are two very different numbers here ────────────────────────────
#
# There used to be one: MEMORY_MAX_CHARS = 2200, applied to the whole store. It
# was a *storage* limit, and it existed only because the entire memory was
# pasted into the system prompt on every connect — so growing the memory grew
# every single request. When it filled, _trim_to_limit() deleted the oldest
# entries and printed one line to a console nobody reads. A memory described as
# "deeply remembers projects, preferences and personal context" was in practice
# two pages long, and quietly forgot your sister's name after a few weeks.
#
# Storage and prompt budget are now separate concerns:
#
#   MEMORY_MAX_CHARS  — a runaway guard, not a feature limit. Nothing normal
#                       reaches it; a bug writing in a loop does.
#   PROMPT_CORE_CHARS — what actually rides in the system prompt every session.
#                       Smaller than the old whole-memory dump, so sessions
#                       start *faster* than before, not slower.
#
# Everything above the core stays on disk and is fetched on demand by the
# recall_memory tool — see search_memory() and format_memory_for_prompt().
MEMORY_MAX_CHARS  = 200_000
PROMPT_CORE_CHARS = 900
PROMPT_INDEX_CHARS = 420
# Most entries any one category may contribute to the core block, so a person
# with forty stored preferences still gets their sister into the prompt.
PROMPT_MAX_PER_CATEGORY = 6

def _empty_memory() -> dict:
    return {
        "identity":      {},
        "preferences":   {},
        "projects":      {},
        "relationships": {},
        "wishes":        {},
        "notes":         {},
    }

def load_memory() -> dict:
    if not MEMORY_PATH.exists():
        return _empty_memory()
    with _lock:
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                base = _empty_memory()
                for key in base:
                    if key not in data:
                        data[key] = {}
                return data
            return _empty_memory()
        except Exception as e:
            print(f"[Memory] ⚠️ Load error: {e}")
            return _empty_memory()

def _all_entries(memory: dict) -> list[tuple]:
    entries = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                entries.append((cat, key, entry))
    return entries


# Set by main.py so a trim can reach the activity log. Deleting something a
# person told you and mentioning it only on stdout is how a memory loses trust.
_trim_notifier = None


def set_trim_notifier(fn) -> None:
    """Register a callable(str) that surfaces trims to the user."""
    global _trim_notifier
    _trim_notifier = fn


# Set by main.py so store/retrieve can join the event bus from whatever
# thread called us (the Living Memory Brain's sync channel — the activity
# ring + dashboard see memory traffic live). Observability only: same
# never-raise contract as the trim notifier; unbound = silent no-op.
_memory_event_hook = None


def set_memory_event_hook(fn) -> None:
    """Register callable(event_type, data) fired after a store or a recall.
    event_type is "memory_stored" | "memory_retrieved"; data carries only
    non-value metadata (key/category/hits) — memory contents never leave."""
    global _memory_event_hook
    _memory_event_hook = fn


def _fire_memory_event(event_type: str, data: dict) -> None:
    if _memory_event_hook:
        try:
            _memory_event_hook(event_type, data)
        except Exception:
            pass


def _trim_to_limit(memory: dict) -> dict:
    if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
        return memory
    entries = _all_entries(memory)
    entries.sort(key=lambda t: t[2].get("updated", "0000-00-00"))
    dropped = []
    for cat, key, _ in entries:
        if len(json.dumps(memory, ensure_ascii=False)) <= MEMORY_MAX_CHARS:
            break
        del memory[cat][key]
        dropped.append(f"{cat}/{key}")
        print(f"[Memory] 🗑️  Trimmed {cat}/{key}")
    if dropped and _trim_notifier:
        try:
            _trim_notifier(
                f"SYS: Memory full — forgot {len(dropped)} oldest entries "
                f"({', '.join(dropped[:3])}{'…' if len(dropped) > 3 else ''})"
            )
        except Exception:
            pass
    return memory

def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically (temp file + os.replace).

    A crash mid-write can no longer truncate long_term.json or a session
    transcript to partial JSON — readers always see the old or new file.
    Caller must hold _lock when writing MEMORY_PATH.
    """
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_memory(memory: dict) -> None:
    if not isinstance(memory, dict):
        return
    memory = _trim_to_limit(memory)
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        _atomic_write(
            MEMORY_PATH,
            json.dumps(memory, indent=2, ensure_ascii=False),
        )


def _truncate_value(val: str) -> str:
    if isinstance(val, str) and len(val) > MAX_VALUE_LENGTH:
        return val[:MAX_VALUE_LENGTH].rstrip() + "…"
    return val


def _recursive_update(target: dict, updates: dict) -> bool:
    changed = False
    for key, value in updates.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, dict) and "value" not in value:
            if key not in target or not isinstance(target[key], dict):
                target[key] = {}
                changed = True
            if _recursive_update(target[key], value):
                changed = True
        else:
            new_val  = _truncate_value(str(value["value"] if isinstance(value, dict) else value))
            entry    = {"value": new_val, "updated": datetime.now().strftime("%Y-%m-%d")}
            existing = target.get(key, {})
            if not isinstance(existing, dict) or existing.get("value") != new_val:
                target[key] = entry
                changed = True
    return changed


def update_memory(memory_update: dict) -> dict:
    if not isinstance(memory_update, dict) or not memory_update:
        return load_memory()
    memory = load_memory()
    if _recursive_update(memory, memory_update):
        save_memory(memory)
        print(f"[Memory] 💾 Saved: {list(memory_update.keys())}")
    return memory

def _entry_value(entry) -> str:
    """Accept both the {'value': ..., 'updated': ...} shape and a bare string,
    because early versions of the store wrote plain strings."""
    if isinstance(entry, dict):
        return str(entry.get("value", "") or "").strip()
    return str(entry or "").strip()


def _pretty(key: str) -> str:
    return key.replace("_", " ").strip()


# Identity is always in the prompt; these categories compete for the remaining
# budget by recency.
_CATEGORY_LABELS = {
    "preferences":   "Preferences",
    "projects":      "Active projects / goals",
    "relationships": "People in their life",
    "wishes":        "Wishes / plans",
    "notes":         "Notes",
}

_IDENTITY_FIELDS = ["name", "age", "birthday", "city", "job",
                    "language", "school", "nationality"]


def format_memory_for_prompt(memory: dict | None) -> str:
    """Build the memory block that goes into the system prompt.

    This used to dump everything. It now sends three things:

      1. IDENTITY  - always, in full. It is small, and it is wrong for the
         assistant to have to look up your name.
      2. RECENT    - the most recently updated entries from every other
         category, up to PROMPT_CORE_CHARS. Recency is the cheapest useful
         relevance signal available without embeddings.
      3. AN INDEX  - the *keys* of everything else, values omitted.

    Point 3 is what makes recall work at all. A model cannot decide to look
    something up if it does not know the thing exists: with only points 1 and 2,
    "who is Ayse?" would get "I don't know" while ayse_sister sat on disk
    unread. The index costs a few hundred characters and turns recall from a
    gamble into a lookup.

    Net effect on latency: this block is SMALLER than the old full dump, so
    every session connects with fewer tokens. Occasionally the model spends one
    extra round trip on recall_memory - covered by the acknowledgment it
    already speaks before any slow step."""
    if not memory:
        return ""

    core_lines: list[str] = []

    # 1. Identity - always, in full
    identity = memory.get("identity", {}) or {}
    for field in _IDENTITY_FIELDS:
        val = _entry_value(identity.get(field))
        if not val:
            continue
        if field == "language":
            # Language is set permanently — show it as the active language,
            # not as an observation about the past.
            core_lines.append(f"Primary language: {val}")
        else:
            core_lines.append(f"{field.title()}: {val}")
    for key, entry in identity.items():
        if key in _IDENTITY_FIELDS:
            continue
        val = _entry_value(entry)
        if val:
            core_lines.append(f"{_pretty(key).title()}: {val}")

    # 2. Everything else, most recently updated first
    rest: list[tuple[str, str, str, str]] = []   # (updated, cat, key, value)
    for cat in _CATEGORY_LABELS:
        for key, entry in (memory.get(cat, {}) or {}).items():
            val = _entry_value(entry)
            if not val:
                continue
            updated = (entry.get("updated", "") if isinstance(entry, dict) else "") or "0000-00-00"
            rest.append((updated, cat, key, val))
    rest.sort(key=lambda t: t[0], reverse=True)

    used    = sum(len(l) + 1 for l in core_lines)
    shown: dict[str, list[str]] = {}
    overflow: dict[str, list[str]] = {}

    # Recency decides order, but no single category may take the whole budget.
    # Without the cap, someone with forty stored preferences gets a prompt that
    # is forty preferences and not one person's name — the categories that
    # matter most in conversation are also the ones that change least often, so
    # pure recency systematically buries them.
    per_cat_used: dict[str, int] = {}
    for _updated, cat, key, val in rest:
        line = f"  - {_pretty(key).title()}: {val}"
        if (per_cat_used.get(cat, 0) < PROMPT_MAX_PER_CATEGORY
                and used + len(line) + 1 <= PROMPT_CORE_CHARS):
            shown.setdefault(cat, []).append(line)
            per_cat_used[cat] = per_cat_used.get(cat, 0) + 1
            used += len(line) + 1
        else:
            overflow.setdefault(cat, []).append(_pretty(key))

    # The index is a table of contents, so it is interleaved across categories
    # rather than continuing in recency order. Sorted by recency it would list
    # twenty-four preferences before the first relationship, and the one entry
    # the index exists for — the old fact the model has no other way to know
    # about — would fall off the end.
    indexed: list[str] = []
    if overflow:
        cats  = [c for c in _CATEGORY_LABELS if overflow.get(c)]
        cursor = {c: 0 for c in cats}
        while cats:
            for cat in list(cats):
                i = cursor[cat]
                if i >= len(overflow[cat]):
                    cats.remove(cat)
                    continue
                indexed.append(overflow[cat][i])
                cursor[cat] = i + 1

    for cat, label in _CATEGORY_LABELS.items():
        if shown.get(cat):
            core_lines.append("")
            core_lines.append(f"{label}:")
            core_lines.extend(shown[cat])

    if not core_lines and not indexed:
        return ""

    out = [
        "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, never recite like a list]",
        *core_lines,
    ]

    # 3. The index of what is on disk but not in this prompt
    if indexed:
        budget, names = PROMPT_INDEX_CHARS, []
        for n in indexed:
            if budget - len(n) - 2 < 0:
                break
            names.append(n)
            budget -= len(n) + 2
        if names:
            out.append("")
            out.append(
                "[ALSO REMEMBERED — values not shown here. Call recall_memory "
                "with a keyword to read any of these before saying you do not know]"
            )
            out.append(", ".join(names)
                       + (f" (+{len(indexed) - len(names)} more)"
                          if len(indexed) > len(names) else ""))

    return "\n".join(out) + "\n"


# ── Recall ────────────────────────────────────────────────────────────────────

def _score(query_words: list[str], cat: str, key: str, value: str) -> int:
    """Cheap lexical relevance. No embeddings, no network, no model call - this
    runs in well under a millisecond, which is the entire point: recall must
    cost one model round trip, never two."""
    hay_key = _pretty(key).lower()
    hay_val = value.lower()
    # Also include the raw key (with underscores) for exact matches
    hay_raw = key.lower()
    score   = 0
    for w in query_words:
        if not w:
            continue
        if w == hay_key or w == hay_raw:
            score += 10
        elif w in hay_key or w in hay_raw:
            score += 6
        if w in hay_val:
            score += 3
        if w in cat:
            score += 1
    return score


def search_memory(query: str, limit: int = 8) -> str:
    """Find stored facts matching `query`. Backs the recall_memory tool.

    An empty query is treated as "show me everything you know", capped - the
    model asks that when the user says "what do you remember about me?"."""
    memory = load_memory()
    words  = [w for w in re.split(r"[^\w]+", (query or "").lower()) if len(w) > 1]

    rows: list[tuple[int, str, str, str]] = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue                     # skip 'sessions', which is a list
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            s = _score(words, cat, key, val) if words else 1
            if s > 0:
                rows.append((s, cat, key, val))

    if not rows:
        _fire_memory_event("memory_retrieved", {"hits": 0})
        return (f"Nothing stored about '{query}'." if query
                else "I have not stored anything about this person yet.")

    rows.sort(key=lambda r: (-r[0], r[2]))
    lines = [f"{cat}/{_pretty(key)}: {val}" for _s, cat, key, val in rows[:max(1, limit)]]
    head  = (f"Stored facts matching '{query}':" if query
             else "Everything currently stored:")
    more  = (f"\n(+{len(rows) - len(lines)} more — search with a narrower keyword)"
             if len(rows) > len(lines) else "")
    _fire_memory_event("memory_retrieved", {"hits": len(rows)})
    return head + "\n" + "\n".join(lines) + more


def all_entries_for_ui() -> list[dict]:
    """Flat list for the memory panel: what JARVIS knows, and when it learned it.
    Sorted newest first so the panel opens on what changed most recently."""
    memory = load_memory()
    rows = []
    for cat, items in memory.items():
        if not isinstance(items, dict):
            continue
        for key, entry in items.items():
            val = _entry_value(entry)
            if not val:
                continue
            rows.append({
                "category": cat,
                "key":      key,
                "value":    val,
                "updated":  (entry.get("updated", "") if isinstance(entry, dict) else ""),
            })
    rows.sort(key=lambda r: (r["updated"] or "0000-00-00"), reverse=True)
    return rows

def is_sensitive_key_value(key: str, value: str) -> bool:
    """Detect if a memory key or value contains sensitive credentials."""
    sensitive_keywords = {
        "password", "passcode", "pin", "ssn", "social_security", "credit_card",
        "debit_card", "cvv", "secret", "auth_token", "private_key", "api_key"
    }
    combined = f"{key} {value}".lower()
    return any(kw in combined for kw in sensitive_keywords)


def remember(key: str, value: str, category: str = "notes") -> str:
    if is_sensitive_key_value(key, value):
        return (
            "Refused to silently store sensitive information (passwords/credentials). "
            "Sensitive data cannot be persisted into memory."
        )
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        category = "notes"
    update_memory({category: {key: {"value": value}}})
    _fire_memory_event("memory_stored", {"key": key, "category": category})
    return f"Remembered: {category}/{key} = {value}"


def forget(key: str, category: str = "notes") -> str:
    memory = load_memory()
    cat    = memory.get(category, {})
    if key in cat:
        del cat[key]
        memory[category] = cat
        save_memory(memory)
        return f"Forgotten: {category}/{key}"
    return f"Not found: {category}/{key}"


def forget_matching(query: str) -> str:
    """Find and delete memory entries whose key or value matches query."""
    if not query or not query.strip():
        return "Please specify what you want me to forget."

    memory = load_memory()
    q_words = [w for w in re.split(r"[^\w]+", query.lower()) if len(w) > 1]
    if not q_words:
        return f"Could not parse query '{query}' to forget."

    deleted_count = 0
    deleted_keys = []

    for cat, items in list(memory.items()):
        if not isinstance(items, dict):
            continue
        to_delete = []
        for key, entry in items.items():
            val = _entry_value(entry)
            combined = f"{key} {val}".lower()
            if any(w in combined for w in q_words):
                to_delete.append(key)
        for key in to_delete:
            del memory[cat][key]
            deleted_count += 1
            deleted_keys.append(f"{cat}/{key}")

    if deleted_count > 0:
        save_memory(memory)
        return f"Forgotten {deleted_count} item(s): {', '.join(deleted_keys[:5])}"

    return f"Nothing found in memory matching '{query}' to forget."


forget_memory = forget


# ── Short-term conversation turn buffer ───────────────────────────────────────
_short_term_turns: list[dict] = []


def add_short_term_turn(role: str, text: str) -> None:
    with _lock:
        _short_term_turns.append({"role": role, "text": text, "timestamp": datetime.now().isoformat()})
        while len(_short_term_turns) > 20:
            _short_term_turns.pop(0)


def get_short_term_turns() -> list[dict]:
    with _lock:
        return list(_short_term_turns)


def clear_short_term_turns() -> None:
    with _lock:
        _short_term_turns.clear()


# ── Session memory ─────────────────────────────────────────────────────────────

_DEFAULT_SESSION_MAX = 3   # safety cap — in practice 0-1 entries after pop


def _session_max() -> int:
    """Session-summary cap; override with JARVIS_SESSION_MAX (>=1)."""
    try:
        return max(1, int(os.environ.get("JARVIS_SESSION_MAX", _DEFAULT_SESSION_MAX)))
    except (TypeError, ValueError):
        return _DEFAULT_SESSION_MAX


def save_session_summary(summary: str, language: str = "") -> None:
    """Append a 1-2 sentence session summary to long_term.json['sessions']."""
    summary = (summary or "").strip()
    if not summary:
        return
    memory   = load_memory()
    sessions = memory.get("sessions", [])
    if not isinstance(sessions, list):
        sessions = []
    entry: dict = {
        "date":    datetime.now().strftime("%Y-%m-%d"),
        "summary": summary[:280],
    }
    if language:
        entry["language"] = language
    sessions.append(entry)
    memory["sessions"] = sessions[-_session_max():]
    with _lock:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(MEMORY_PATH, json.dumps(memory, indent=2, ensure_ascii=False))
    print(f"[Memory] 📝 Session saved ({entry['date']}): {summary[:60]}…")


def pop_last_session() -> dict | None:
    """
    Return AND remove the most recent session entry.
    Calling this consumes the entry so it is never repeated in future briefings.
    """
    with _lock:
        if not MEMORY_PATH.exists():
            return None
        try:
            memory   = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            sessions = memory.get("sessions", [])
            if not isinstance(sessions, list) or not sessions:
                return None
            entry = sessions.pop()          # remove the last entry
            memory["sessions"] = sessions
            _atomic_write(MEMORY_PATH, json.dumps(memory, indent=2, ensure_ascii=False))
            return entry
        except Exception as e:
            print(f"[Memory] ⚠️ pop_last_session error: {e}")
            return None


# ── Session history (persisted transcripts) ─────────────────────────────────

_SESSIONS_DIR = MEMORY_PATH.parent / "sessions"
_session_seq = 0


def save_session_log(turns: list[str], summary: str = "", language: str = "") -> None:
    """Persist the full conversation transcript to memory/sessions/<timestamp>.json."""
    if not turns:
        return
    global _session_seq
    _session_seq += 1
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M-%S") + f"_{_session_seq:04d}"
    entry = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "summary": (summary or "")[:280],
        "language": language,
        "turn_count": len(turns),
        "turns": list(turns),
    }
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = _SESSIONS_DIR / f"{ts}.json"
    try:
        _atomic_write(path, json.dumps(entry, indent=2, ensure_ascii=False))
        print(f"[Memory] 💾 Session transcript saved: {path.name} ({len(turns)} turns)")
    except Exception as e:
        print(f"[Memory] ⚠️ save_session_log error: {e}")


def get_saved_sessions(limit: int = 20) -> list[dict]:
    """Return persisted session transcripts, newest first.

    Each entry has: date, time, summary, language, turn_count, turns (list[str]).
    """
    if not _SESSIONS_DIR.exists():
        return []
    sessions = []
    for path in sorted(_SESSIONS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["_file"] = path.name
            sessions.append(data)
        except Exception:
            continue
    return sessions


def get_session_count() -> int:
    """Return the number of persisted session transcripts."""
    if not _SESSIONS_DIR.exists():
        return 0
    return sum(1 for _ in _SESSIONS_DIR.glob("*.json"))

def get_memory_stats() -> dict:
    """Return statistics about the stored memory.

    Returns a dict with:
      - total: total number of entries across all categories
      - by_category: {category: count} for each category
      - oldest: date string of the oldest entry
      - newest: date string of the newest entry
      - size_bytes: approximate file size on disk
      - sessions: number of stored session summaries
    """
    memory = load_memory()
    total = 0
    by_cat = {}
    dates = []

    for cat, items in memory.items():
        if cat == "sessions":
            continue
        if not isinstance(items, dict):
            continue
        count = 0
        for key, entry in items.items():
            val = _entry_value(entry)
            if val:
                count += 1
                updated = ""
                if isinstance(entry, dict):
                    updated = entry.get("updated", "")
                if updated:
                    dates.append(updated)
        by_cat[cat] = count
        total += count

    sessions = memory.get("sessions", [])
    session_count = len(sessions) if isinstance(sessions, list) else 0

    size = 0
    if MEMORY_PATH.exists():
        try:
            size = MEMORY_PATH.stat().st_size
        except Exception:
            pass

    return {
        "total": total,
        "by_category": by_cat,
        "oldest": min(dates) if dates else "",
        "newest": max(dates) if dates else "",
        "size_bytes": size,
        "sessions": session_count,
    }


def export_memory() -> str:
    """Export all memories as a JSON string for backup or transfer.

    Returns a JSON string containing all categories and entries.
    Session summaries are excluded (they are transient).
    """
    memory = load_memory()
    export = {}
    for cat, items in memory.items():
        if cat == "sessions":
            continue
        if isinstance(items, dict):
            export[cat] = items
    return json.dumps(export, indent=2, ensure_ascii=False)


def import_memory(json_str: str, merge: bool = True) -> str:
    """Import memories from a JSON string.

    Args:
        json_str: JSON string with category→key→entry structure.
        merge: If True, merge with existing memory. If False, replace entirely.

    Returns a status message.
    """
    if not json_str or not json_str.strip():
        return "No data provided for import."

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if not isinstance(data, dict):
        return "Import data must be a JSON object with category keys."

    valid_cats = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    imported = 0
    skipped = 0

    if merge:
        memory = load_memory()
    else:
        memory = _empty_memory()

    for cat, items in data.items():
        if cat not in valid_cats:
            skipped += 1
            continue
        if not isinstance(items, dict):
            skipped += 1
            continue
        if cat not in memory:
            memory[cat] = {}
        for key, entry in items.items():
            if isinstance(entry, dict) and "value" in entry:
                val = _truncate_value(str(entry["value"]))
                if is_sensitive_key_value(key, val):
                    skipped += 1
                    continue
                memory[cat][key] = {
                    "value": val,
                    "updated": entry.get("updated", datetime.now().strftime("%Y-%m-%d")),
                }
                imported += 1
            elif isinstance(entry, str):
                val = _truncate_value(entry)
                if is_sensitive_key_value(key, val):
                    skipped += 1
                    continue
                memory[cat][key] = {
                    "value": val,
                    "updated": datetime.now().strftime("%Y-%m-%d"),
                }
                imported += 1
            else:
                skipped += 1

    save_memory(memory)
    return f"Imported {imported} entries ({skipped} skipped due to invalid data or sensitive content)."


def clear_category(category: str) -> str:
    """Delete all entries in a specific category.

    Returns a status message with the count of deleted entries.
    """
    valid = {"identity", "preferences", "projects", "relationships", "wishes", "notes"}
    if category not in valid:
        return f"Unknown category: '{category}'. Valid: {', '.join(sorted(valid))}"

    memory = load_memory()
    items = memory.get(category, {})
    if not isinstance(items, dict) or not items:
        return f"Category '{category}' is already empty."

    count = len(items)
    memory[category] = {}
    save_memory(memory)
    return f"Cleared {count} entries from '{category}'."


def clear_all_memory(confirm_key: str = "") -> str:
    """Delete ALL stored memories. Requires confirmation_key='YES_DELETE_ALL'.

    This is a destructive operation. The confirmation key prevents accidental
    bulk deletion.
    """
    if confirm_key != "YES_DELETE_ALL":
        return (
            "This will delete ALL stored memories. "
            "Call again with confirm_key='YES_DELETE_ALL' to proceed."
        )

    memory = _empty_memory()
    save_memory(memory)
    return "All memories have been cleared."


def get_memory_age(key: str, category: str = "notes") -> str:
    """Return a human-readable age string for a memory entry.

    Examples: "today", "2 days ago", "about 3 weeks ago", "unknown".
    """
    memory = load_memory()
    cat = memory.get(category, {})
    entry = cat.get(key)
    if not entry or not isinstance(entry, dict):
        return "not found"

    updated = entry.get("updated", "")
    if not updated:
        return "unknown"

    try:
        entry_date = datetime.strptime(updated, "%Y-%m-%d")
    except ValueError:
        return "unknown"

    now = datetime.now()
    delta = now - entry_date
    days = delta.days

    if days == 0:
        return "today"
    elif days == 1:
        return "yesterday"
    elif days < 7:
        return f"{days} days ago"
    elif days < 30:
        weeks = days // 7
        return f"about {'one' if weeks == 1 else weeks} week{'s' if weeks != 1 else ''} ago"
    elif days < 365:
        months = days // 30
        return f"about {'one' if months == 1 else months} month{'s' if months != 1 else ''} ago"
    else:
        years = days // 365
        return f"about {'one' if years == 1 else years} year{'s' if years != 1 else ''} ago"