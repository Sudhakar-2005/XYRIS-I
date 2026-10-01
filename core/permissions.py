"""
Permission ledger — Phase 40 (§3.17, part-17 LAYER 3).

A TOOL dict's ``permissions: list[str]`` says which capabilities the tool
exercises; main._execute_tool refuses to dispatch when any of them is not
granted. Grants come in two tiers, per part-17 ("session-level vs
permanent"):

  session   — this process only, never written anywhere
  permanent — config/permissions.json (gitignored, user-owned)

A missing config file revokes nothing: a fresh clone behaves exactly like
pre-Phase 40. An unreadable/corrupt config file fails CLOSED — every
permission in the vocabulary is revoked until the file is fixed, so a typo
can never silently re-open a capability. Unknown permission names are never
granted.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# The closed vocabulary. Every permission a TOOL declares must appear here;
# core/action_loader.policy_from_meta rejects anything else at load time.
PERMISSION_VOCAB = (
    "capture",      # screenshots / camera frames
    "desktop",      # OS input, settings, notifications
    "filesystem",   # read/write/move files
    "generation",   # external image-generation API
    "memory",       # persisted assistant memory / audit records
    "messaging",    # sending messages or notifications to people
    "network",      # any data leaving this machine
    "process",      # launching, scheduling, or stopping processes
)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "permissions.json"


class PermissionLedger:
    """The granted-permission set for one session, backed by a permanent tier.

    ``load=False`` skips the config file entirely (nothing revoked) — the
    default everything-granted state used by module-level fallbacks and
    tests that never had a config file in the first place.
    """

    def __init__(self, config_path: Path | str | None = None,
                 load: bool = True) -> None:
        self._path = (Path(config_path) if config_path is not None
                      else DEFAULT_CONFIG_PATH)
        self._lock = threading.Lock()
        self._session_revoked: set[str] = set()
        self._perm_revoked: set[str] = set()
        if load:
            self._load()

    # ── permanent tier ─────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return                      # default: nothing revoked
        except OSError as e:
            logger.error("[Permissions] cannot read %s — failing closed: %s",
                         self._path, e)
            self._fail_closed()
            return
        try:
            data = json.loads(raw)
            revoked = data.get("revoked", []) if isinstance(data, dict) else None
            if not isinstance(revoked, list) or \
                    any(not isinstance(x, str) for x in revoked):
                raise ValueError('expected {"revoked": [..]}')
        except (ValueError, json.JSONDecodeError) as e:
            logger.error("[Permissions] %s is corrupt (%s) — failing closed "
                         "(every permission revoked until it is fixed)",
                         self._path, e)
            self._fail_closed()
            return
        with self._lock:
            self._perm_revoked = {x for x in revoked if x in PERMISSION_VOCAB}
            self._session_revoked = set(self._perm_revoked)

    def _fail_closed(self) -> None:
        with self._lock:
            self._perm_revoked = set(PERMISSION_VOCAB)
            self._session_revoked = set(PERMISSION_VOCAB)

    def _write(self) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("w", encoding="utf-8") as f:
                json.dump({"revoked": sorted(self._perm_revoked)}, f, indent=2)
            return True
        except OSError as e:
            logger.error("[Permissions] cannot write %s: %s", self._path, e)
            return False

    # ── queries ────────────────────────────────────────────────────────
    def is_granted(self, name: str) -> bool:
        if name not in PERMISSION_VOCAB:
            return False                 # unknown is never granted
        with self._lock:
            return name not in self._session_revoked

    def revoked(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._session_revoked)

    # ── changes ────────────────────────────────────────────────────────
    def grant(self, name: str, permanent: bool = False) -> bool:
        if name not in PERMISSION_VOCAB:
            return False
        with self._lock:
            self._session_revoked.discard(name)
            if permanent:
                self._perm_revoked.discard(name)
                return self._write()
        return True

    def revoke(self, name: str, permanent: bool = False) -> bool:
        if name not in PERMISSION_VOCAB:
            return False
        with self._lock:
            self._session_revoked.add(name)
            if permanent:
                self._perm_revoked.add(name)
                return self._write()
        return True
