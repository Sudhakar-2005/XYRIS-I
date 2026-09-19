"""
Tests for core/verifier.py — Verifier Engine.
"""

import os
import tempfile
import pytest
from core.verifier import (
    Verifier, VerificationRule, VerificationResult, VerificationStatus,
    VerificationStrategy,
)


@pytest.fixture
def verifier():
    return Verifier()


@pytest.fixture
def file_rule():
    return VerificationRule(
        tool_name="write_file",
        strategy=VerificationStrategy.FILE_EXISTS,
    )


@pytest.fixture
def exit_code_rule():
    return VerificationRule(
        tool_name="run_cmd",
        strategy=VerificationStrategy.EXIT_CODE,
    )


@pytest.fixture
def output_parse_rule():
    return VerificationRule(
        tool_name="parse_tool",
        strategy=VerificationStrategy.OUTPUT_PARSE,
        success_indicators=["success", "done", "completed"],
        failure_indicators=["error", "failed", "exception"],
    )


@pytest.fixture
def custom_rule():
    def check(args, output):
        if output and output.get("verified"):
            return VerificationResult(
                status=VerificationStatus.VERIFIED,
                confidence=1.0,
                message="Custom check passed",
            )
        return VerificationResult(
            status=VerificationStatus.FAILED,
            confidence=0.0,
            message="Custom check failed",
        )

    return VerificationRule(
        tool_name="custom_tool",
        strategy=VerificationStrategy.CUSTOM,
        check_fn=check,
    )


class TestVerifier:
    def test_register_rule(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        rule = verifier.get_rule("write_file")
        assert rule is not None
        assert rule.strategy == VerificationStrategy.FILE_EXISTS

    def test_unregister_rule(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        assert verifier.unregister_rule("write_file") is True
        assert verifier.get_rule("write_file") is None

    def test_unregister_nonexistent(self, verifier):
        assert verifier.unregister_rule("nonexistent") is False

    def test_get_rule(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        rule = verifier.get_rule("write_file")
        assert rule.tool_name == "write_file"

    def test_get_rule_not_found(self, verifier):
        assert verifier.get_rule("nonexistent") is None

    def test_verify_no_rule(self, verifier):
        result = verifier.verify("unknown_tool", {}, None)
        assert result.status == VerificationStatus.UNVERIFIABLE
        assert result.confidence == 0.0
        assert "No verification rule" in result.message

    def test_verify_file_exists_existing(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            result = verifier.verify("write_file", {"file_path": path})
            assert result.status == VerificationStatus.VERIFIED
            assert result.confidence == 1.0
        finally:
            os.unlink(path)

    def test_verify_file_exists_nonexistent(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        result = verifier.verify(
            "write_file", {"file_path": "/nonexistent/file.txt"}
        )
        assert result.status == VerificationStatus.FAILED
        assert result.confidence == 0.9

    def test_verify_file_exists_no_path(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        result = verifier.verify("write_file", {})
        assert result.status == VerificationStatus.UNVERIFIABLE

    def test_verify_file_exists_alt_key(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            result = verifier.verify("write_file", {"path": path})
            assert result.status == VerificationStatus.VERIFIED
        finally:
            os.unlink(path)

    def test_verify_exit_code_success(self, verifier, exit_code_rule):
        verifier.register_rule(exit_code_rule)
        result = verifier.verify(
            "run_cmd", {}, {"exit_code": 0}
        )
        assert result.status == VerificationStatus.VERIFIED
        assert result.details["exit_code"] == 0

    def test_verify_exit_code_failure(self, verifier, exit_code_rule):
        verifier.register_rule(exit_code_rule)
        result = verifier.verify(
            "run_cmd", {}, {"exit_code": 1}
        )
        assert result.status == VerificationStatus.FAILED
        assert result.details["exit_code"] == 1

    def test_verify_exit_code_no_code(self, verifier, exit_code_rule):
        verifier.register_rule(exit_code_rule)
        result = verifier.verify("run_cmd", {}, {})
        assert result.status == VerificationStatus.UNVERIFIABLE

    def test_verify_exit_code_returncode_key(self, verifier, exit_code_rule):
        verifier.register_rule(exit_code_rule)
        result = verifier.verify(
            "run_cmd", {}, {"returncode": 0}
        )
        assert result.status == VerificationStatus.VERIFIED

    def test_verify_output_parse_success(self, verifier, output_parse_rule):
        verifier.register_rule(output_parse_rule)
        result = verifier.verify(
            "parse_tool", {}, "Task completed successfully"
        )
        assert result.status == VerificationStatus.VERIFIED
        assert result.confidence == 0.7

    def test_verify_output_parse_failure(self, verifier, output_parse_rule):
        verifier.register_rule(output_parse_rule)
        result = verifier.verify(
            "parse_tool", {}, "An error occurred during execution"
        )
        assert result.status == VerificationStatus.FAILED
        assert result.confidence == 0.8

    def test_verify_output_parse_no_match(self, verifier, output_parse_rule):
        verifier.register_rule(output_parse_rule)
        result = verifier.verify("parse_tool", {}, "some random output")
        assert result.status == VerificationStatus.PARTIAL
        assert result.confidence == 0.3

    def test_verify_output_parse_no_output(self, verifier, output_parse_rule):
        verifier.register_rule(output_parse_rule)
        result = verifier.verify("parse_tool", {}, None)
        assert result.status == VerificationStatus.UNVERIFIABLE

    def test_verify_custom_success(self, verifier, custom_rule):
        verifier.register_rule(custom_rule)
        result = verifier.verify(
            "custom_tool", {}, {"verified": True}
        )
        assert result.status == VerificationStatus.VERIFIED
        assert result.confidence == 1.0

    def test_verify_custom_failure(self, verifier, custom_rule):
        verifier.register_rule(custom_rule)
        result = verifier.verify(
            "custom_tool", {}, {"verified": False}
        )
        assert result.status == VerificationStatus.FAILED

    def test_verify_custom_no_checker(self, verifier):
        rule = VerificationRule(
            tool_name="bad_tool",
            strategy=VerificationStrategy.CUSTOM,
        )
        verifier.register_rule(rule)
        result = verifier.verify("bad_tool", {}, None)
        assert result.status == VerificationStatus.UNVERIFIABLE
        assert "not provided" in result.message

    def test_stats_initial(self, verifier):
        stats = verifier.stats
        assert stats["verifications"] == 0
        assert stats["successes"] == 0
        assert stats["failures"] == 0
        assert stats["unverifiable"] == 0
        assert stats["rules"] == 0

    def test_stats_after_register(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        assert verifier.stats["rules"] == 1

    def test_stats_after_verify(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        with tempfile.NamedTemporaryFile(delete=False) as f:
            path = f.name
        try:
            verifier.verify("write_file", {"file_path": path})
        finally:
            os.unlink(path)

        stats = verifier.stats
        assert stats["verifications"] == 1
        assert stats["successes"] == 1
        assert stats["failures"] == 0

    def test_stats_after_failure(self, verifier, file_rule):
        verifier.register_rule(file_rule)
        verifier.verify(
            "write_file", {"file_path": "/nonexistent/file.txt"}
        )
        stats = verifier.stats
        assert stats["verifications"] == 1
        assert stats["failures"] == 1

    def test_stats_after_unverifiable(self, verifier):
        verifier.verify("unknown_tool", {}, None)
        stats = verifier.stats
        assert stats["verifications"] == 1
        assert stats["unverifiable"] == 1
