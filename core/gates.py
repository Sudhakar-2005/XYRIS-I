"""The single gate for every execution path (brain roadmap Phase 1).

JarvisLive._execute_tool (chat) and core/automation/adapters.build_tool_call
(automation nodes) both decide HERE — the adapter mirror used to re-implement
the rules by hand and had already lost the shutdown_jarvis user-turn rule.

Order is fixed and enforced by tests: availability first, consent second.
  forbidden → permission → user-turn → confirm availability → busy → consent

evaluate_gates decides (pure, no I/O, no prints); each caller executes its
own confirmation mechanics — the chat path parks on core/confirm's banner and
dispatches from its worker thread, the automation path awaits an async
wrapper — and both share the refusal wording below so the model never gets
two different sentences for the same rule.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Decisions
ALLOW = "allow"
DENY = "deny"
CONFIRM = "confirm"

# Rules (denials)
FORBIDDEN = "forbidden"
PERMISSION = "permission"
USER_TURN = "user_turn"
GATE_UNAVAILABLE = "gate_unavailable"
BUSY = "busy"


@dataclass(frozen=True)
class GateDecision:
    decision: str                       # allow | deny | confirm
    rule: str = ""                      # which rule fired ("" when allowed)
    denied: tuple = field(default_factory=tuple)

    @property
    def allowed(self) -> bool:
        return self.decision == ALLOW


def evaluate_gates(name: str, policy, *, ledger=None,
                   heard_user_text: bool = False,
                   busy: bool = False,
                   confirm_available: bool = True) -> GateDecision:
    """Apply the tool's policy against the live state.

    ledger=None means no ledger is in play (everything granted — identical
    to a missing permission config file). heard_user_text defaults False so
    a caller that forgets to report real user input FAILS CLOSED on
    shutdown_jarvis rather than open. busy = a confirmation banner is
    already on screen; confirm_available = a gate exists to park on.
    """
    if getattr(policy, "risk", "safe") == "forbidden":
        return GateDecision(DENY, FORBIDDEN)

    denied = ()
    perms = getattr(policy, "permissions", ()) or ()
    if perms and ledger is not None:
        denied = tuple(p for p in perms if not ledger.is_granted(p))
        if denied:
            return GateDecision(DENY, PERMISSION, denied=denied)

    if getattr(policy, "requires_confirmation", False):
        if name == "shutdown_jarvis" and not heard_user_text:
            # Fail closed: the model must not end the session on its own
            # right after the briefing, with nobody having said a word.
            return GateDecision(DENY, USER_TURN)
        if not confirm_available:
            return GateDecision(DENY, GATE_UNAVAILABLE)
        if busy:
            return GateDecision(DENY, BUSY)
        return GateDecision(CONFIRM, "confirmation")

    return GateDecision(ALLOW)


def message_for(rule: str, name: str, denied=()) -> str:
    """The model-facing refusal for a denied rule — one wording per rule,
    shared by both execution paths."""
    if rule == FORBIDDEN:
        return (f"I cannot run '{name}' — it is marked forbidden "
                f"on this system. Nothing was done.")
    if rule == PERMISSION:
        return (f"I cannot run '{name}' — the required "
                f"permission(s) are not granted on this system: "
                f"{', '.join(denied)}. Nothing was done. The "
                f"user can re-enable them in "
                f"config/permissions.json.")
    if rule == USER_TURN:
        return ("Refused: shutdown_jarvis requires the user to "
                "have spoken or typed something in this session "
                "first. The startup briefing does not count. "
                "Nothing was done — do not call this tool again "
                "until the user explicitly asks to end the "
                "session.")
    if rule == GATE_UNAVAILABLE:
        return (f"'{name}' requires a human confirmation "
                f"and no confirmation gate is available. "
                f"Nothing was done.")
    if rule == BUSY:
        return ("There is already a confirmation waiting on "
                "screen — another request is already waiting "
                "behind it. Answer that one first; nothing was "
                "done.")
    return ""
