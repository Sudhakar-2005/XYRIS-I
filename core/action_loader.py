"""
Action discovery, validation, and dispatch — the built-in twin of plugin_loader.

Every actions/*.py that exposes a module-level ``TOOL`` dict is auto-discovered
here, exactly like a drop-in plugin, so main.py never has to hardcode a tool
declaration or a dispatch branch for it. Adding a new bundled action is then the
same one-file operation as writing a plugin: define ``TOOL`` and a handler.

``TOOL`` shape (see actions/open_app.py for a live example):

    TOOL = {
        "name":        "open_app",              # unique, ^[a-zA-Z_][a-zA-Z0-9_]{0,63}$
        "description":  "...",                   # what Gemini reads to route the call
        "parameters":  {"type": "OBJECT", ...}, # Gemini function-declaration schema
        "handler":      open_app,                # the callable to run
    }

OPTIONALLY, a TOOL may carry six V3 dispatch-metadata keys — risk, timeout,
requires_confirmation, supports_undo, verification, permissions (see
ToolPolicy below). They are enforced by main.py's dispatcher and are never
sent to Gemini.

The handler is invoked through signature introspection: it receives ``parameters``
plus whichever of ``player`` / ``speak`` / ``response`` / ``session_memory`` it
actually declares — so existing action signatures work unchanged.

Discovery runs once at startup; import errors, validation errors, and name
collisions are logged and the offending file is skipped — they NEVER raise out
of discover_actions() and never abort the scan of the remaining files.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.permissions import PERMISSION_VOCAB

_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,63}$")
_DEFAULT_PARAMS = {"type": "OBJECT", "properties": {}}
_CTX_KEYS = ("player", "speak", "response", "session_memory")


# ── V3 dispatch metadata (Phase 17, Phase 40) ─────────────────────────────────
# Every TOOL dict may carry six keys alongside name/description/parameters:
#
#   risk                  "safe" | "caution" | "dangerous" | "forbidden"
#                         (the vocabulary of core.tool_registry.Risk, kept as
#                         plain strings so TOOL dicts stay JSON-shaped)
#   timeout               seconds the dispatcher will wait for the tool
#   requires_confirmation True → the dispatcher parks the call behind the
#                         human CONFIRM/CANCEL gate instead of running it
#   supports_undo         True → the handler pushes core/undo.py entries
#   verification          "none" | "self_check" (the tool checks its own
#                         output — e.g. generate_image re-reads the saved file)
#                         | "verifier" (core/verifier.py runs the TOOL's own
#                         callable `verify` after dispatch — M8; declaring this
#                         mode REQUIRES a `verify` key, and vice versa)
#   permissions           list of capabilities the tool exercises, from
#                         core.permissions.PERMISSION_VOCAB (Phase 40). The
#                         dispatcher refuses the call while any of them is
#                         revoked in the session's PermissionLedger.
#
# These keys are NEVER sent to Gemini: get_tool_declarations() builds the
# function_declarations itself, so the API schema stays exactly as before and
# the metadata remains ours to enforce. Enforcement lives in main.py's
# _execute_tool (forbidden → refuse, permission revoked → refuse, gated →
# confirm gate, timeout → wait_for).
DEFAULT_TIMEOUT_S = 60
MIN_TIMEOUT_S = 1
MAX_TIMEOUT_S = 3600
RISK_LEVELS = ("safe", "caution", "dangerous", "forbidden")
VERIFICATION_MODES = ("none", "self_check", "verifier")


@dataclass(frozen=True)
class ToolPolicy:
    """Resolved dispatch policy for one tool. Immutable: the dispatcher reads
    it, never writes it."""
    risk: str = "safe"
    timeout: int = DEFAULT_TIMEOUT_S
    requires_confirmation: bool = False
    supports_undo: bool = False
    verification: str = "none"
    permissions: tuple[str, ...] = ()


def policy_from_meta(meta: dict | None) -> tuple[Optional[ToolPolicy], str]:
    """Build a ToolPolicy from a raw metadata dict.

    Missing keys fall back to the defaults (a tool that declares nothing is
    ordinary and safe). Present-but-invalid values return (None, error) —
    loud failure at load time, fail-closed at dispatch time. Returns
    (ToolPolicy, "") for None/empty input.
    """
    if not meta:
        return ToolPolicy(), ""
    errs = []

    risk = meta.get("risk", "safe")
    if not isinstance(risk, str) or risk.lower() not in RISK_LEVELS:
        errs.append(f"risk {risk!r} not one of {RISK_LEVELS}")
        risk = "safe"

    timeout = meta.get("timeout", DEFAULT_TIMEOUT_S)
    if isinstance(timeout, bool) or not isinstance(timeout, int) \
            or not (MIN_TIMEOUT_S <= timeout <= MAX_TIMEOUT_S):
        errs.append(f"timeout {timeout!r} must be an int in "
                    f"{MIN_TIMEOUT_S}..{MAX_TIMEOUT_S}")
        timeout = DEFAULT_TIMEOUT_S

    bools = {}
    for key in ("requires_confirmation", "supports_undo"):
        val = meta.get(key, False)
        if not isinstance(val, bool):
            errs.append(f"{key} {val!r} must be a bool")
            val = False
        bools[key] = val

    verification = meta.get("verification", "none")
    if not isinstance(verification, str) or verification not in VERIFICATION_MODES:
        errs.append(f"verification {verification!r} not one of {VERIFICATION_MODES}")
        verification = "none"

    perms = meta.get("permissions", ())
    if not isinstance(perms, (list, tuple)) or \
            any(not isinstance(p, str) or p not in PERMISSION_VOCAB
                for p in perms):
        errs.append(f"permissions {perms!r} must be a list of "
                    f"{PERMISSION_VOCAB}")
        perms = ()

    if errs:
        return None, "; ".join(errs)
    return ToolPolicy(risk=risk.lower(), timeout=timeout,
                      requires_confirmation=bools["requires_confirmation"],
                      supports_undo=bools["supports_undo"],
                      verification=verification,
                      permissions=tuple(dict.fromkeys(perms))), ""


# A tool may declare that the model should NOT be held up waiting for it.
# `behavior` goes to the API with the declaration; `scheduling` decides when the
# eventual result is allowed back into the conversation:
#   WHEN_IDLE  — wait for a gap in the speech (the sane default)
#   SILENT     — record it, do not prompt a reply (the tool already announced)
#   INTERRUPT  — cut in immediately (only when the answer cannot wait)
_BEHAVIORS = ("BLOCKING", "NON_BLOCKING")
_SCHEDULING = ("WHEN_IDLE", "SILENT", "INTERRUPT")


def _opt_upper(value, allowed: tuple[str, ...]) -> Optional[str]:
    v = str(value or "").strip().upper()
    return v if v in allowed else None


@dataclass
class ActionRecord:
    name: str
    description: str = ""
    parameters: dict = field(default_factory=lambda: dict(_DEFAULT_PARAMS))
    handler: Optional[Callable] = None
    file: str = ""
    valid: bool = False
    error: str = ""
    behavior: Optional[str] = None     # None = the API's default (blocking)
    scheduling: Optional[str] = None   # None = the API's default (WHEN_IDLE)
    policy: ToolPolicy = field(default_factory=ToolPolicy)  # V3 dispatch metadata
    verify_fn: Optional[Callable] = None  # M8: post-dispatch probe (§3.13)


class ActionRegistry:
    def __init__(self, actions: dict[str, ActionRecord], logger: Callable[[str], None]):
        self._actions = actions          # name -> ActionRecord, VALID entries only
        self._all_records: list[ActionRecord] = []
        self._logger = logger

    # -- called by main.py at LiveConnectConfig build time --
    def get_tool_declarations(self) -> list[dict]:
        out = []
        for rec in self._actions.values():
            decl = {"name": rec.name, "description": rec.description,
                    "parameters": rec.parameters}
            if rec.behavior:
                decl["behavior"] = rec.behavior
            out.append(decl)
        return out

    def has(self, name: str) -> bool:
        return name in self._actions

    def scheduling(self, name: str) -> Optional[str]:
        """How this action's result should re-enter the conversation, if it said."""
        rec = self._actions.get(name)
        return rec.scheduling if rec else None

    def policy(self, name: str) -> Optional[ToolPolicy]:
        """V3 dispatch policy for `name`, or None when it is not an action."""
        rec = self._actions.get(name)
        return rec.policy if rec else None

    def record(self, name: str) -> Optional["ActionRecord"]:
        """The loaded record for `name` (valid entries only), or None —
        the public reader for metadata like parameters/valid that used to
        be reachable only through the private _all_records list."""
        return self._actions.get(name)

    def verify_fn(self, name: str) -> Optional[Callable]:
        """M8: the post-dispatch probe for `name`, or None when the action
        declares no verification (main.py registers the callable with
        core/verifier.py; the declaration itself never carries it)."""
        rec = self._actions.get(name)
        return rec.verify_fn if rec else None

    def policies(self) -> dict[str, ToolPolicy]:
        """name → policy for every valid action (coverage tests iterate this)."""
        return {name: rec.policy for name, rec in self._actions.items()}

    def describe(self, name: str) -> str:
        """The declaration description — used for the confirm-gate detail."""
        rec = self._actions.get(name)
        return rec.description if rec else ""

    def names(self) -> set[str]:
        return set(self._actions.keys())

    # -- called by main.py from _execute_tool --
    def run(self, name: str, parameters: dict, ctx: dict | None = None) -> str:
        rec = self._actions.get(name)
        if rec is None or not rec.valid:
            return f"Action '{name}' is not available."
        try:
            return _call_handler(rec.handler, parameters, ctx or {}) or "Done."
        except Exception as e:
            self._logger(f"Action '{name}' crashed during run(): {e}")
            traceback.print_exc()
            return f"Tool '{name}' failed: {e}"


def _call_handler(fn: Callable, parameters: dict, ctx: dict) -> str:
    """Invoke the handler passing only the context kwargs it actually declares
    (or all of them if it has **kwargs), so each action's existing signature
    works unchanged."""
    sig = inspect.signature(fn)
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    kwargs = {}
    for key in _CTX_KEYS:
        if has_var_kw or key in sig.parameters:
            kwargs[key] = ctx.get(key)
    return fn(parameters=parameters, **kwargs)


def _validate(module, filename: str) -> ActionRecord:
    """Returns an ActionRecord; .valid=False + .error set on any problem. Never raises."""
    tool = getattr(module, "TOOL", None)
    if not isinstance(tool, dict):
        return ActionRecord(name=Path(filename).stem, file=filename,
                            error="No module-level TOOL dict (not a discoverable action).")

    name = tool.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return ActionRecord(name=str(name or Path(filename).stem), file=filename,
                            error="TOOL['name'] missing or not a valid identifier.")

    description = tool.get("description")
    if not isinstance(description, str) or not description.strip():
        return ActionRecord(name=name, file=filename,
                            error="TOOL['description'] missing or empty.")

    parameters = tool.get("parameters", _DEFAULT_PARAMS)
    if not isinstance(parameters, dict) or parameters.get("type") != "OBJECT":
        return ActionRecord(name=name, file=filename,
                            error="TOOL['parameters'] must be a dict with \"type\": \"OBJECT\".")

    handler = tool.get("handler")
    if not callable(handler):
        return ActionRecord(name=name, file=filename,
                            error="TOOL['handler'] missing or not callable.")

    policy, perr = policy_from_meta(tool)
    if policy is None:
        return ActionRecord(name=name, file=filename,
                            error=f"TOOL metadata invalid: {perr}.")

    # M8: the verifier mode and the probe callable must agree — a declared
    # mode with nothing to run, or a probe nobody will call, is a silent hole
    # in exactly the place the audit says never to have one.
    verify_fn = tool.get("verify")
    if verify_fn is not None and not callable(verify_fn):
        return ActionRecord(name=name, file=filename,
                            error="TOOL['verify'] must be callable.")
    if policy.verification == "verifier" and verify_fn is None:
        return ActionRecord(name=name, file=filename,
                            error="TOOL declares verification='verifier' "
                                  "but has no callable 'verify'.")
    if verify_fn is not None and policy.verification != "verifier":
        return ActionRecord(name=name, file=filename,
                            error=f"TOOL provides 'verify' but "
                                  f"verification={policy.verification!r} — "
                                  f"set verification='verifier'.")

    return ActionRecord(name=name, description=description.strip(), parameters=parameters,
                        handler=handler, file=filename, valid=True, error="",
                        behavior=_opt_upper(tool.get("behavior"), _BEHAVIORS),
                        scheduling=_opt_upper(tool.get("scheduling"), _SCHEDULING),
                        policy=policy, verify_fn=verify_fn)


def discover_actions(actions_dir: Path, reserved_names: set[str] | None = None,
                     logger: Callable[[str], None] = print) -> ActionRegistry:
    """
    Scans actions_dir for *.py files (skips files starting with '_'). A file is
    only treated as an action if it exposes a module-level TOOL dict; files
    without one (shared helpers, capture-only modules) are silently ignored.
    Import/validation errors and name collisions are logged and the file is
    skipped — they NEVER raise out of this function.
    """
    reserved = reserved_names or set()
    actions_dir.mkdir(parents=True, exist_ok=True)
    valid: dict[str, ActionRecord] = {}
    all_records: list[ActionRecord] = []

    files = sorted(actions_dir.glob("*.py"), key=lambda p: p.name)  # deterministic order
    for path in files:
        if path.name.startswith("_"):
            continue
        try:
            module_name = f"actions.{path.stem}"
            # Reuse the already-imported module when present so handlers are the
            # same objects the rest of the app holds.
            module = sys.modules.get(module_name)
            if module is None:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("could not build import spec")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    sys.modules.pop(module_name, None)
                    raise

            if getattr(module, "TOOL", None) is None:
                continue   # not an action file — a helper/capture-only module

            rec = _validate(module, path.name)

            if rec.valid and rec.name in reserved:
                rec = ActionRecord(name=rec.name, file=path.name,
                                   error=f"Name '{rec.name}' collides with a reserved core tool — rejected.")
            elif rec.valid and rec.name in valid:
                other = valid[rec.name].file
                rec = ActionRecord(name=rec.name, file=path.name,
                                   error=f"Name '{rec.name}' already used by action '{other}' — rejected.")

        except Exception as e:
            rec = ActionRecord(name=path.stem, file=path.name,
                               error=f"Failed to load: {e}")
            traceback.print_exc()

        all_records.append(rec)
        if rec.valid:
            valid[rec.name] = rec
            logger(f"Action loaded: {rec.name} ({path.name})")
        else:
            # Only log a rejection if the file actually tried to be an action.
            logger(f"Action rejected: {path.name} — {rec.error}")

    registry = ActionRegistry(valid, logger)
    registry._all_records = all_records
    logger(f"Action discovery complete: {len(valid)} active.")
    return registry
