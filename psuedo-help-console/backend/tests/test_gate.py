"""Unit tests for the Quality Gate evaluator."""
import pytest
from backend.app.gate import evaluate_gate, GateReason, GateResult


def test_gate_pass_no_findings():
    """Gate passes when there are no findings and no step failures."""
    result = evaluate_gate({}, [])
    assert result.outcome == "pass"
    assert len(result.reasons) == 0


def test_gate_pass_only_low_findings():
    """Gate passes when only low findings exist."""
    findings = [
        {"severity": "low", "file": "app.py", "line": 13, "title": "Style hint"}
    ]
    result = evaluate_gate({}, findings)
    assert result.outcome == "pass"
    assert len(result.reasons) == 0


def test_gate_warn_medium_findings():
    """Gate warns when only medium severity findings exist."""
    findings = [
        {"severity": "medium", "file": "app.py", "line": 20, "title": "Missing rate limit"}
    ]
    result = evaluate_gate({}, findings)
    assert result.outcome == "warn"
    assert len(result.reasons) == 1
    assert "medium" in result.reasons[0].rule
    assert "Missing rate limit" in result.reasons[0].detail


def test_gate_fail_critical_findings():
    """Gate fails when critical findings exist."""
    findings = [
        {"severity": "critical", "file": "app.py", "line": 19, "title": "SQL Injection"}
    ]
    result = evaluate_gate({}, findings)
    assert result.outcome == "fail"
    assert len(result.reasons) == 1
    assert "SQL Injection" in result.reasons[0].detail
    assert "app.py:19" in result.reasons[0].detail


def test_gate_fail_high_findings():
    """Gate fails when high findings exist."""
    findings = [
        {"severity": "high", "file": "app.py", "line": 16, "title": "Hardcoded secret"}
    ]
    result = evaluate_gate({}, findings)
    assert result.outcome == "fail"
    assert len(result.reasons) == 1
    assert "Hardcoded secret" in result.reasons[0].detail


def test_gate_fail_multiple_critical_and_high():
    """Gate returns human readable count and details for multiple severe findings."""
    findings = [
        {"severity": "critical", "file": "app.py", "line": 19, "title": "SQL Injection"},
        {"severity": "high", "file": "app.py", "line": 16, "title": "Hardcoded secret"},
        {"severity": "low", "file": "app.py", "line": 5, "title": "Unused import"}
    ]
    result = evaluate_gate({}, findings)
    assert result.outcome == "fail"
    assert "2 critical/high finding(s)" in result.reasons[0].detail


def test_gate_fail_required_step_failed():
    """Gate fails if a required step (e.g., build_and_tests) failed."""
    steps = {
        "build_and_tests": {"status": "failed", "message": "Tests failed: 2 errors"}
    }
    result = evaluate_gate(steps, [])
    assert result.outcome == "fail"
    assert result.reasons[0].rule == "required_step_failed"
    assert "build_and_tests" in result.reasons[0].detail


def test_gate_with_steps_list_format():
    """Gate supports list-of-steps input format as well."""
    steps = [
        {"name": "security_scan", "status": "failed", "message": "High vulnerability detected"}
    ]
    result = evaluate_gate(steps, [])
    assert result.outcome == "fail"
    assert "security_scan" in result.reasons[0].detail
