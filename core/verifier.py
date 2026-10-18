"""
Verifier Engine — Post-action verification.

Part of XYRIS I Intelligence Architecture V0.1
Phase 1: Runtime Foundation

XYRIS must NEVER claim success merely because an action was issued.
Verification is a first-class intelligence capability.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class VerificationStatus(Enum):
    """Status of verification."""
    VERIFIED = "verified"
    FAILED = "failed"
    UNVERIFIABLE = "unverifiable"
    PARTIAL = "partial"


class VerificationStrategy(Enum):
    """How to verify a tool's result."""
    NONE = "none"               # No verification possible
    EXIT_CODE = "exit_code"     # Check process exit code
    FILE_EXISTS = "file_exists" # Check if file was created
    FILE_CHANGED = "file_changed"  # Check if file was modified
    APP_RUNNING = "app_running" # Check if app is running
    DOM_CHECK = "dom_check"     # Check browser DOM
    SCREENSHOT = "screenshot"   # Compare screenshots
    OUTPUT_PARSE = "output_parse"  # Parse output for success indicators
    CUSTOM = "custom"           # Custom verification function


@dataclass
class VerificationResult:
    """Result of a verification check."""
    status: VerificationStatus
    confidence: float = 0.0  # 0.0 to 1.0
    message: str = ""
    details: dict = field(default_factory=dict)
    duration_ms: float = 0.0

    @property
    def is_success(self) -> bool:
        """Check if verification succeeded."""
        return self.status == VerificationStatus.VERIFIED


def verdict_dict(vres: VerificationResult, tool: str = "") -> dict:
    """The structured verdict — the machine-readable twin of the prose note.

    Consumed by the automation adapters (a passed=False verdict ends the
    node as an error even when the text looks fine). passed is False only
    for FAILED: PARTIAL and UNVERIFIABLE stay honest uncertainty — not
    fake success, and not a fabricated failure either.
    """
    return {
        "tool": str(tool),
        "status": str(vres.status.value),
        "passed": vres.status != VerificationStatus.FAILED,
        "confidence": float(vres.confidence or 0.0),
        "detail": (vres.message or "").strip(),
    }


@dataclass
class VerificationRule:
    """A rule for verifying a specific tool's result."""
    tool_name: str
    strategy: VerificationStrategy
    check_fn: Optional[Callable] = None  # For CUSTOM strategy
    success_indicators: list[str] = field(default_factory=list)
    failure_indicators: list[str] = field(default_factory=list)
    timeout_ms: float = 5000.0

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "strategy": self.strategy.value,
            "success_indicators": self.success_indicators,
            "failure_indicators": self.failure_indicators,
            "timeout_ms": self.timeout_ms,
        }


class Verifier:
    """Verification engine for tool execution results.

    Ensures that actions actually produced the expected outcomes
    before claiming success.

    Usage:
        verifier = Verifier()

        # Register verification rules
        verifier.register_rule(VerificationRule(
            tool_name="open_app",
            strategy=VerificationStrategy.APP_RUNNING,
        ))

        # Verify a tool result
        result = verifier.verify(
            tool_name="open_app",
            args={"app_name": "notepad"},
            output={"pid": 1234},
        )

        if result.is_success:
            print("Action verified!")
        else:
            print(f"Verification failed: {result.message}")
    """

    def __init__(self):
        self._rules: dict[str, VerificationRule] = {}
        self._custom_checkers: dict[str, Callable] = {}
        self._stats = {
            "verifications": 0,
            "successes": 0,
            "failures": 0,
            "unverifiable": 0,
        }

    def register_rule(self, rule: VerificationRule) -> None:
        """Register a verification rule for a tool."""
        self._rules[rule.tool_name] = rule
        if rule.check_fn:
            self._custom_checkers[rule.tool_name] = rule.check_fn

    def unregister_rule(self, tool_name: str) -> bool:
        """Unregister a verification rule."""
        if tool_name in self._rules:
            del self._rules[tool_name]
            self._custom_checkers.pop(tool_name, None)
            return True
        return False

    def get_rule(self, tool_name: str) -> Optional[VerificationRule]:
        """Get verification rule for a tool."""
        return self._rules.get(tool_name)

    def verify(self, tool_name: str, args: dict, output: Any = None) -> VerificationResult:
        """Verify a tool execution result.

        Args:
            tool_name: Name of the tool that was executed
            args: Arguments passed to the tool
            output: Output from the tool execution

        Returns:
            VerificationResult with status and confidence
        """
        start_time = time.time()
        self._stats["verifications"] += 1

        rule = self._rules.get(tool_name)
        if rule is None:
            # No verification rule - assume unverifiable
            self._stats["unverifiable"] += 1
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message=f"No verification rule for tool '{tool_name}'",
            )

        # Execute verification based on strategy
        try:
            result = self._execute_verification(rule, args, output)
        except Exception as e:
            logger.error("Verification error for %s: %s", tool_name, e)
            result = VerificationResult(
                status=VerificationStatus.FAILED,
                confidence=0.0,
                message=f"Verification error: {e}",
            )

        result.duration_ms = (time.time() - start_time) * 1000

        # Update stats
        if result.status == VerificationStatus.VERIFIED:
            self._stats["successes"] += 1
        elif result.status == VerificationStatus.FAILED:
            self._stats["failures"] += 1
        else:
            self._stats["unverifiable"] += 1

        return result

    def _execute_verification(
        self, rule: VerificationRule, args: dict, output: Any
    ) -> VerificationResult:
        """Execute verification based on strategy."""
        if rule.strategy == VerificationStrategy.NONE:
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="No verification configured",
            )

        elif rule.strategy == VerificationStrategy.CUSTOM:
            if rule.check_fn:
                return rule.check_fn(args, output)
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="Custom checker not provided",
            )

        elif rule.strategy == VerificationStrategy.FILE_EXISTS:
            return self._verify_file_exists(args, output)

        elif rule.strategy == VerificationStrategy.FILE_CHANGED:
            return self._verify_file_changed(args, output)

        elif rule.strategy == VerificationStrategy.APP_RUNNING:
            return self._verify_app_running(args, output)

        elif rule.strategy == VerificationStrategy.EXIT_CODE:
            return self._verify_exit_code(args, output)

        elif rule.strategy == VerificationStrategy.OUTPUT_PARSE:
            return self._verify_output_parse(args, output, rule)

        elif rule.strategy == VerificationStrategy.SCREENSHOT:
            return self._verify_screenshot(args, output)

        elif rule.strategy == VerificationStrategy.DOM_CHECK:
            return self._verify_dom(args, output)

        return VerificationResult(
            status=VerificationStatus.UNVERIFIABLE,
            confidence=0.0,
            message=f"Strategy {rule.strategy.value} not implemented",
        )

    def _verify_file_exists(self, args: dict, output: Any) -> VerificationResult:
        """Verify a file was created."""
        file_path = args.get("file_path") or args.get("path")
        if not file_path:
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="No file path in args",
            )

        exists = os.path.exists(file_path)
        return VerificationResult(
            status=VerificationStatus.VERIFIED if exists else VerificationStatus.FAILED,
            confidence=1.0 if exists else 0.9,
            message=f"File {'exists' if exists else 'not found'}: {file_path}",
        )

    def _verify_file_changed(self, args: dict, output: Any) -> VerificationResult:
        """Verify a file was modified (exists and has content)."""
        file_path = args.get("file_path") or args.get("path")
        if not file_path:
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="No file path in args",
            )

        if not os.path.exists(file_path):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                confidence=0.9,
                message=f"File not found: {file_path}",
            )

        size = os.path.getsize(file_path)
        return VerificationResult(
            status=VerificationStatus.VERIFIED if size > 0 else VerificationStatus.PARTIAL,
            confidence=0.8,
            message=f"File exists, size: {size} bytes",
            details={"size": size},
        )

    def _verify_app_running(self, args: dict, output: Any) -> VerificationResult:
        """Verify an application is running."""
        app_name = args.get("app_name") or args.get("command")
        if not app_name:
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="No app name in args",
            )

        # Check if output contains PID or success indicator
        if output and isinstance(output, dict):
            if output.get("pid") or output.get("status") == "success":
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    confidence=0.8,
                    message=f"App launch reported success: {app_name}",
                )

        return VerificationResult(
            status=VerificationStatus.PARTIAL,
            confidence=0.3,
            message=f"Cannot verify app running: {app_name}",
        )

    def _verify_exit_code(self, args: dict, output: Any) -> VerificationResult:
        """Verify command exit code."""
        if output and isinstance(output, dict):
            exit_code = output.get("exit_code", output.get("returncode"))
            if exit_code is not None:
                success = exit_code == 0
                return VerificationResult(
                    status=VerificationStatus.VERIFIED if success else VerificationStatus.FAILED,
                    confidence=0.9,
                    message=f"Exit code: {exit_code}",
                    details={"exit_code": exit_code},
                )

        return VerificationResult(
            status=VerificationStatus.UNVERIFIABLE,
            confidence=0.0,
            message="No exit code in output",
        )

    def _verify_output_parse(
        self, args: dict, output: Any, rule: VerificationRule
    ) -> VerificationResult:
        """Verify by parsing output for success/failure indicators."""
        if not output:
            return VerificationResult(
                status=VerificationStatus.UNVERIFIABLE,
                confidence=0.0,
                message="No output to parse",
            )

        output_str = str(output).lower()

        # Check failure indicators first
        for indicator in rule.failure_indicators:
            if indicator.lower() in output_str:
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    confidence=0.8,
                    message=f"Failure indicator found: {indicator}",
                )

        # Check success indicators
        for indicator in rule.success_indicators:
            if indicator.lower() in output_str:
                return VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    confidence=0.7,
                    message=f"Success indicator found: {indicator}",
                )

        return VerificationResult(
            status=VerificationStatus.PARTIAL,
            confidence=0.3,
            message="No matching indicators in output",
        )

    def _verify_screenshot(self, args: dict, output: Any) -> VerificationResult:
        """Verify via screenshot comparison."""
        # Placeholder for future implementation
        return VerificationResult(
            status=VerificationStatus.UNVERIFIABLE,
            confidence=0.0,
            message="Screenshot verification not yet implemented",
        )

    def _verify_dom(self, args: dict, output: Any) -> VerificationResult:
        """Verify via DOM inspection."""
        # Placeholder for future implementation
        return VerificationResult(
            status=VerificationStatus.UNVERIFIABLE,
            confidence=0.0,
            message="DOM verification not yet implemented",
        )

    @property
    def stats(self) -> dict:
        """Return verification statistics."""
        return {
            **self._stats,
            "rules": len(self._rules),
        }
