"""Capability discovery (Phase 1/15): the live catalog of everything a
workflow may call.

Built from the EXISTING registries — ActionRegistry, PluginRegistry and the
inline tool metadata main.py injects. There is no second tool registry: this
module only *reads* what the app already exposes. Absent registries (tests)
produce an empty catalog rather than an error.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


PLUGIN_RISK_DEFAULT = "safe"


@dataclass(frozen=True)
class Capability:
    name: str
    kind: str                       # action | plugin | inline
    description: str = ""
    parameters: dict = field(default_factory=dict)
    risk: str = "safe"
    timeout: int = 60
    requires_confirmation: bool = False
    supports_undo: bool = False
    verification: str = "none"
    permissions: tuple[str, ...] = ()
    available: bool = True
    note: str = ""

    @property
    def required_params(self) -> tuple[str, ...]:
        req = (self.parameters or {}).get("required") or ()
        return tuple(str(r) for r in req)

    @property
    def properties(self) -> dict:
        props = (self.parameters or {}).get("properties") or {}
        return props if isinstance(props, dict) else {}

    def to_prompt_dict(self, max_desc: int = 160) -> dict:
        """Bounded, secret-free projection for LLM prompts."""
        return {
            "name": self.name, "kind": self.kind,
            "description": (self.description or "")[:max_desc],
            "params": sorted(self.properties.keys()),
            "required": list(self.required_params),
            "risk": self.risk, "permissions": list(self.permissions),
            "confirmation": self.requires_confirmation,
            "available": self.available,
        }


class CapabilityCatalog:
    """Snapshot of callable capabilities; refresh() re-reads the registries."""

    def __init__(self, action_registry: Any = None, plugin_registry: Any = None,
                 inline_meta: dict | None = None,
                 policy_resolver: Callable[[str], Any] | None = None) -> None:
        self._actions = action_registry
        self._plugins = plugin_registry
        self._inline = dict(inline_meta or {})
        self._policy_resolver = policy_resolver
        self._caps: dict[str, Capability] = {}
        self.refresh()

    # ------------------------------------------------------------- building

    def refresh(self) -> None:
        caps: dict[str, Capability] = {}

        if self._actions is not None:
            try:
                names = list(self._actions.names())
            except Exception:
                names = []
            for name in names:
                try:
                    policy = self._actions.policy(name)
                    desc = self._actions.describe(name) or ""
                    rec = None
                    records = getattr(self._actions, "_all_records", None)
                    if isinstance(records, dict):
                        rec = records.get(name)
                    params = dict(getattr(rec, "parameters", None) or {}) if rec else {}
                    available = bool(getattr(rec, "valid", True)) if rec else True
                except Exception:
                    continue
                caps[name] = Capability(
                    name=name, kind="action", description=desc, parameters=params,
                    risk=getattr(policy, "risk", "safe"),
                    timeout=int(getattr(policy, "timeout", 60) or 60),
                    requires_confirmation=bool(getattr(policy, "requires_confirmation", False)),
                    supports_undo=bool(getattr(policy, "supports_undo", False)),
                    verification=str(getattr(policy, "verification", "none") or "none"),
                    permissions=tuple(getattr(policy, "permissions", ()) or ()),
                    available=available,
                )

        if self._plugins is not None:
            try:
                decls = list(self._plugins.get_tool_declarations())
            except Exception:
                decls = []
            for d in decls:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name", ""))
                if not name:
                    continue
                caps[name] = Capability(
                    name=name, kind="plugin",
                    description=str(d.get("description", "")),
                    parameters=dict(d.get("parameters") or {}),
                    risk=PLUGIN_RISK_DEFAULT, timeout=60,
                    requires_confirmation=False, supports_undo=False,
                    verification="none", permissions=(),
                    available=True,
                    note="plugin — risk resolved by workflow risk engine",
                )

        for name, meta in self._inline.items():
            if name in caps or not isinstance(meta, dict):
                continue
            caps[name] = Capability(
                name=name, kind="inline",
                description=str(meta.get("description", "")),
                parameters={},
                risk=str(meta.get("risk", "safe")),
                timeout=int(meta.get("timeout", 60) or 60),
                requires_confirmation=bool(meta.get("requires_confirmation", False)),
                supports_undo=bool(meta.get("supports_undo", False)),
                verification=str(meta.get("verification", "none") or "none"),
                permissions=tuple(meta.get("permissions") or ()),
            )

        self._caps = caps

    # -------------------------------------------------------------- queries

    def get(self, name: str) -> Capability | None:
        return self._caps.get(name)

    def names(self) -> list[str]:
        return sorted(self._caps)

    def tools(self) -> list[Capability]:
        return [self._caps[k] for k in sorted(self._caps)]

    def tool_nodes(self) -> list[Capability]:
        return [c for c in self.tools() if c.kind in ("action", "plugin")]

    def has(self, name: str) -> bool:
        return name in self._caps

    def prompt_list(self, kinds: Iterable[str] = ("action", "plugin"),
                    limit: int = 200) -> list[dict]:
        kinds = tuple(kinds)
        return [c.to_prompt_dict() for c in self.tools()
                if c.kind in kinds][:limit]

    def __len__(self) -> int:
        return len(self._caps)
