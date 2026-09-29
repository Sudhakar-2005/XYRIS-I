"""
File Intelligence action — smart file operations beyond basic CRUD.

Provides duplicate detection, content search, batch rename, recent files,
directory size analysis, temp cleanup, and file type statistics.
"""
import hashlib
import os
import re
import time
from pathlib import Path
from collections import Counter

try:
    import send2trash
    _SEND2TRASH = True
except ImportError:
    _SEND2TRASH = False


def _resolve_path(raw: str) -> Path:
    """Reuse file_controller's shortcut system."""
    from actions.file_controller import _resolve_path as _fc_resolve
    return _fc_resolve(raw)


def _is_safe(target: Path) -> bool:
    from actions.file_controller import _is_safe_path
    return _is_safe_path(target)


def _format_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


def _safe_iterdir(d: Path, max_items: int = 10000):
    """Iterate directory contents safely with a limit."""
    count = 0
    try:
        for item in d.iterdir():
            yield item
            count += 1
            if count >= max_items:
                break
    except PermissionError:
        pass


def _file_hash(path: Path, chunk_size: int = 8192) -> str:
    """SHA-256 hash of file content."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except (OSError, PermissionError):
        return ""


def _match_pattern(filename: str, pattern: str) -> bool:
    """Simple glob-like pattern matching (* and ?)."""
    try:
        regex_str = "^"
        for ch in pattern:
            if ch == "*":
                regex_str += ".*"
            elif ch == "?":
                regex_str += "."
            else:
                regex_str += re.escape(ch)
        regex_str += "$"
        regex = re.compile(regex_str, re.IGNORECASE)
        return bool(regex.match(filename))
    except re.error:
        return filename.lower() == pattern.lower()


def _try_send2trash(path: Path) -> bool:
    if not _SEND2TRASH:
        return False
    try:
        send2trash.send2trash(str(path))
        return True
    except Exception:
        return False


def file_intelligence(parameters: dict, player=None, session_memory=None) -> str:
    action = parameters.get("action", "").strip().lower()

    try:
        if action == "find_duplicates":
            return _handle_find_duplicates(parameters)
        elif action == "search_content":
            return _handle_search_content(parameters)
        elif action == "recent_files":
            return _handle_recent_files(parameters)
        elif action == "batch_rename":
            return _handle_batch_rename(parameters)
        elif action == "dir_size":
            return _handle_dir_size(parameters)
        elif action == "temp_cleanup":
            return _handle_temp_cleanup(parameters)
        elif action == "file_type_stats":
            return _handle_file_type_stats(parameters)
        else:
            return (
                f"Unknown file intelligence action '{action}'. "
                "Use: find_duplicates, search_content, recent_files, "
                "batch_rename, dir_size, temp_cleanup, file_type_stats."
            )
    except Exception as e:
        return f"File intelligence action failed: {e}"


# ── Handlers ─────────────────────────────────────────────────────────────────


def _handle_find_duplicates(params: dict) -> str:
    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    try:
        max_results = max(1, min(50, int(params.get("max_results", 20))))
    except (TypeError, ValueError):
        max_results = 20

    size_map: dict[int, list[Path]] = {}
    scanned = 0
    for item in target.rglob("*"):
        if item.is_file():
            try:
                sz = item.stat().st_size
                if sz > 0:
                    size_map.setdefault(sz, []).append(item)
            except (OSError, PermissionError):
                pass
            scanned += 1
            if scanned > 5000:
                break

    duplicates: list[tuple[str, list[Path]]] = []
    for sz, files in size_map.items():
        if len(files) < 2:
            continue
        hash_map: dict[str, list[Path]] = {}
        for f in files:
            h = _file_hash(f)
            if h:
                hash_map.setdefault(h, []).append(f)
        for h, group in hash_map.items():
            if len(group) >= 2:
                duplicates.append((h[:12], group))

    if not duplicates:
        return f"No duplicate files found in {target.name}/ (scanned {scanned} files)."

    lines = [f"Found {len(duplicates)} duplicate group(s) in {target.name}/ ({scanned} files scanned):"]
    for i, (h, group) in enumerate(duplicates[:max_results], 1):
        size_str = _format_size(group[0].stat().st_size)
        names = [f.name for f in group[:5]]
        lines.append(f"  {i}. [{size_str}] {', '.join(names)}")
        if len(group) > 5:
            lines.append(f"     ... and {len(group) - 5} more")
        lines.append(f"     Hash: {h}...")

    if len(duplicates) > max_results:
        lines.append(f"  ... and {len(duplicates) - max_results} more groups")

    total_waste = sum(
        sum(f.stat().st_size for f in group[1:])
        for _, group in duplicates
    )
    lines.append(f"\nEstimated recoverable space: {_format_size(total_waste)}")
    return "\n".join(lines)


def _handle_search_content(params: dict) -> str:
    query = params.get("query", "").strip()
    if not query:
        return "Please provide a search query (text to find inside files)."

    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    extension = params.get("extension", "").strip().lower().lstrip(".")
    try:
        max_results = max(1, min(50, int(params.get("max_results", 20))))
    except (TypeError, ValueError):
        max_results = 20

    matches: list[str] = []
    scanned = 0
    search_pattern = f"*.{extension}" if extension else None

    files = target.rglob(search_pattern) if search_pattern else target.rglob("*")
    for item in files:
        if not item.is_file():
            continue
        if item.stat().st_size > 2_000_000:
            continue
        scanned += 1
        try:
            text = item.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    rel = item.relative_to(target) if item.is_relative_to(target) else item
                    matches.append(f"  {rel}:{i}: {line.strip()[:120]}")
                    break
        except (OSError, PermissionError):
            pass
        if len(matches) >= max_results:
            break

    if not matches:
        return f"No matches for '{query}' in {target.name}/ ({scanned} files scanned)."

    header = f"Found {len(matches)} match(es) for '{query}' in {target.name}/:"
    return header + "\n" + "\n".join(matches)


def _handle_recent_files(params: dict) -> str:
    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    try:
        max_results = max(1, min(30, int(params.get("max_results", 10))))
    except (TypeError, ValueError):
        max_results = 10

    mod_or_create = params.get("sort_by", "modified").strip().lower()

    files: list[tuple[float, Path]] = []
    scanned = 0
    for item in target.rglob("*"):
        if item.is_file():
            try:
                st = item.stat()
                ts = st.st_mtime if mod_or_create == "modified" else st.st_ctime
                files.append((ts, item))
            except (OSError, PermissionError):
                pass
            scanned += 1
            if scanned > 5000:
                break

    files.sort(key=lambda x: x[0], reverse=True)
    top = files[:max_results]

    if not top:
        return f"No files found in {target.name}/."

    lines = [f"Most recently {mod_or_create} files in {target.name}/:"]
    for ts, f in top:
        dt = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
        sz = _format_size(f.stat().st_size)
        lines.append(f"  {dt}  {sz:>8}  {f.name}")

    return "\n".join(lines)


def _handle_batch_rename(params: dict) -> str:
    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    find_pattern = params.get("find", "").strip()
    replace_with = params.get("replace", "").strip()
    extension = params.get("extension", "").strip().lower().lstrip(".")

    if not find_pattern and not replace_with:
        return "Please provide a 'find' pattern and/or 'replace' text for batch rename."

    try:
        dry_run = params.get("dry_run", "true").strip().lower() in ("true", "1", "yes")
    except AttributeError:
        dry_run = True

    glob_pattern = f"*.{extension}" if extension else "*"
    renames: list[tuple[Path, Path]] = []

    for item in target.iterdir():
        if not item.is_file():
            continue
        if extension and item.suffix.lower() != f".{extension}":
            continue

        new_name = item.name
        if find_pattern:
            new_name = new_name.replace(find_pattern, replace_with)
        elif replace_with:
            new_name = replace_with + item.name

        if new_name != item.name:
            new_path = item.parent / new_name
            renames.append((item, new_path))

    if not renames:
        return f"No files matched for renaming in {target.name}/."

    if dry_run:
        lines = [f"Dry run — {len(renames)} file(s) would be renamed:"]
        for old, new in renames[:20]:
            lines.append(f"  {old.name} -> {new.name}")
        if len(renames) > 20:
            lines.append(f"  ... and {len(renames) - 20} more")
        lines.append("\nRe-run with dry_run=false to apply.")
        return "\n".join(lines)

    renamed = 0
    for old, new in renames:
        try:
            old.rename(new)
            renamed += 1
        except (OSError, PermissionError):
            pass

    return f"Renamed {renamed} file(s) in {target.name}/."


def _handle_dir_size(params: dict) -> str:
    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."
    if not target.is_dir():
        return f"'{target.name}' is not a directory."

    dir_sizes: dict[str, int] = {}
    file_count = 0
    dir_count = 0
    scanned = 0

    for item in target.rglob("*"):
        scanned += 1
        if scanned > 20000:
            break
        try:
            if item.is_file():
                sz = item.stat().st_size
                file_count += 1
                parent = str(item.parent.relative_to(target))
                dir_sizes[parent] = dir_sizes.get(parent, 0) + sz
            elif item.is_dir():
                dir_count += 1
        except (OSError, PermissionError):
            pass

    if not dir_sizes:
        return f"'{target.name}/' appears to be empty."

    top_dirs = sorted(dir_sizes.items(), key=lambda x: x[1], reverse=True)[:15]
    total = sum(dir_sizes.values())

    lines = [f"Size analysis for {target.name}/ ({_format_size(total)} total):"]
    lines.append(f"  {file_count} files, {dir_count} subdirectories\n")
    lines.append("  Top directories by size:")
    for name, sz in top_dirs:
        pct = (sz / total * 100) if total > 0 else 0
        lines.append(f"    {name:40s} {_format_size(sz):>10s} ({pct:.1f}%)")

    return "\n".join(lines)


def _handle_temp_cleanup(params: dict) -> str:
    raw_path = params.get("path", "downloads")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    try:
        days = max(1, min(365, int(params.get("days", 30))))
    except (TypeError, ValueError):
        days = 30

    try:
        dry_run = params.get("dry_run", "true").strip().lower() in ("true", "1", "yes")
    except AttributeError:
        dry_run = True

    temp_extensions = {".tmp", ".temp", ".log", ".bak", ".old", ".cache",
                       ".part", ".crdownload", ".opdownload"}
    cutoff = time.time() - (days * 86400)
    candidates: list[Path] = []
    scanned = 0

    for item in target.rglob("*"):
        if not item.is_file():
            continue
        scanned += 1
        if scanned > 10000:
            break
        try:
            st = item.stat()
            if st.st_mtime < cutoff:
                if item.suffix.lower() in temp_extensions:
                    candidates.append(item)
        except (OSError, PermissionError):
            pass

    if not candidates:
        return f"No old temp files (>{days} days) found in {target.name}/ ({scanned} files scanned)."

    total_size = sum(f.stat().st_size for f in candidates)

    if dry_run:
        lines = [f"Dry run — {len(candidates)} temp file(s) older than {days} days "
                 f"in {target.name}/ ({_format_size(total_size)}):"]
        for f in candidates[:20]:
            age_days = int((time.time() - f.stat().st_mtime) / 86400)
            lines.append(f"  {f.name} ({_format_size(f.stat().st_size)}, {age_days}d old)")
        if len(candidates) > 20:
            lines.append(f"  ... and {len(candidates) - 20} more")
        lines.append("\nRe-run with dry_run=false to delete (moves to trash).")
        return "\n".join(lines)

    deleted = 0
    for f in candidates:
        if _try_send2trash(f):
            deleted += 1
        else:
            try:
                f.unlink()
                deleted += 1
            except (OSError, PermissionError):
                pass

    return f"Cleaned up {deleted} temp file(s) ({_format_size(total_size)}) from {target.name}/."


def _handle_file_type_stats(params: dict) -> str:
    raw_path = params.get("path", "home")
    target = _resolve_path(raw_path)
    if not _is_safe(target):
        return "That path is outside the safe area."
    if not target.exists():
        return f"Path '{target}' does not exist."

    ext_counter: Counter = Counter()
    size_by_ext: dict[str, int] = {}
    scanned = 0

    for item in target.rglob("*"):
        if item.is_file():
            ext = item.suffix.lower() or "(no extension)"
            ext_counter[ext] += 1
            try:
                size_by_ext[ext] = size_by_ext.get(ext, 0) + item.stat().st_size
            except (OSError, PermissionError):
                pass
            scanned += 1
            if scanned > 10000:
                break

    if not ext_counter:
        return f"No files found in {target.name}/."

    lines = [f"File type distribution in {target.name}/ ({scanned} files):"]
    total_size = sum(size_by_ext.values())
    for ext, count in ext_counter.most_common(15):
        sz = size_by_ext.get(ext, 0)
        pct = (count / scanned * 100) if scanned > 0 else 0
        lines.append(f"  {ext:15s} {count:5d} files  {_format_size(sz):>10s} ({pct:.1f}%)")

    if len(ext_counter) > 15:
        lines.append(f"  ... and {len(ext_counter) - 15} more types")
    lines.append(f"\nTotal: {scanned} files, {_format_size(total_size)}")

    return "\n".join(lines)


TOOL = {
    "name": "file_intelligence",
    "risk": "safe",
    "timeout": 60,
    "requires_confirmation": False,
    "supports_undo": False,
    "verification": "none",
    "description": (
        "Smart file operations beyond basic CRUD. Use this for: "
        "finding duplicate files (by content hash), searching inside files "
        "(grep-like content search), listing recent files, batch renaming "
        "files by pattern, analyzing directory sizes, cleaning temp/old files, "
        "and file type statistics. Trigger phrases: 'find duplicates', "
        "'search inside files', 'recent files', 'batch rename', "
        "'how big is this folder', 'clean temp files', 'file types'. "
        "For basic file operations (list, create, delete, move, copy, read, write), "
        "use file_controller instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": (
                    "The file intelligence action: "
                    "'find_duplicates' (find duplicate files by hash), "
                    "'search_content' (grep-like search inside files), "
                    "'recent_files' (list recently modified/created files), "
                    "'batch_rename' (rename files by find/replace pattern), "
                    "'dir_size' (analyze directory sizes), "
                    "'temp_cleanup' (clean old temp files), "
                    "'file_type_stats' (file type distribution)."
                ),
            },
            "path": {
                "type": "STRING",
                "description": "Directory path or shortcut (desktop, downloads, documents, home). Default: home.",
            },
            "query": {
                "type": "STRING",
                "description": "Text to search for inside files (for search_content).",
            },
            "find": {
                "type": "STRING",
                "description": "Text pattern to find in filenames (for batch_rename).",
            },
            "replace": {
                "type": "STRING",
                "description": "Replacement text (for batch_rename).",
            },
            "extension": {
                "type": "STRING",
                "description": "File extension filter, e.g. 'txt', 'jpg' (for search_content, batch_rename).",
            },
            "max_results": {
                "type": "STRING",
                "description": "Maximum results to return (default varies by action).",
            },
            "days": {
                "type": "STRING",
                "description": "Age in days for temp_cleanup (default: 30).",
            },
            "dry_run": {
                "type": "STRING",
                "description": "Preview only without changes: 'true' (default) or 'false'.",
            },
            "sort_by": {
                "type": "STRING",
                "description": "Sort recent_files by 'modified' (default) or 'created'.",
            },
        },
        "required": ["action"],
    },
    "handler": file_intelligence,
}
