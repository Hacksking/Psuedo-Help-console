"""Tests for Reviewer JSON parsing, validation, and fixture fallback."""
import pytest
from pathlib import Path
from backend.app.reviewer import (
    parse_review_response,
    strip_markdown_fences,
    validate_and_filter_findings,
    load_cached_review,
    review_pr_diff
)


def test_strip_markdown_fences():
    """Verify markdown fences are cleanly stripped."""
    raw = "```json\n{\"summary\": \"ok\", \"findings\": []}\n```"
    stripped = strip_markdown_fences(raw)
    assert stripped == '{"summary": "ok", "findings": []}'

    generic = "```\n{\"summary\": \"ok\", \"findings\": []}\n```"
    assert strip_markdown_fences(generic) == '{"summary": "ok", "findings": []}'


def test_parse_json_plain():
    """Parse plain JSON response."""
    json_str = '{"summary": "test", "findings": []}'
    result = parse_review_response(json_str)
    assert result["summary"] == "test"
    assert result["findings"] == []


def test_parse_json_with_markdown_fence():
    """Parse JSON wrapped in markdown code fence."""
    json_str = """```json
    {
      "summary": "found issues",
      "findings": [
        {
          "severity": "high",
          "file": "app.py",
          "line": 15,
          "title": "Hardcoded secret",
          "explanation": "Secret found",
          "suggested_fix": "use env var",
          "confidence": 0.9
        }
      ]
    }
    ```"""
    result = parse_review_response(json_str)
    assert result["summary"] == "found issues"
    assert len(result["findings"]) == 1
    assert result["findings"][0]["severity"] == "high"


def test_filter_low_confidence_and_missing_files():
    """Findings with confidence < 0.5 or missing files are removed."""
    diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n+print('hello')"
    findings = [
        {
            "severity": "critical",
            "file": "app.py",
            "line": 1,
            "title": "Good finding",
            "explanation": "Expl",
            "confidence": 0.95
        },
        {
            "severity": "critical",
            "file": "app.py",
            "line": 1,
            "title": "Low confidence finding",
            "explanation": "Expl",
            "confidence": 0.3
        },
        {
            "severity": "critical",
            "file": "ghost.py",
            "line": 1,
            "title": "Hallucinated file finding",
            "explanation": "Expl",
            "confidence": 0.99
        }
    ]
    filtered = validate_and_filter_findings(findings, diff)
    assert len(filtered) == 1
    assert filtered[0]["title"] == "Good finding"


def test_cap_and_sort_findings():
    """Findings must be capped at 8 and sorted by severity."""
    diff = "--- a/app.py\n+++ b/app.py"
    findings = [
        {"severity": "low", "file": "app.py", "line": i, "title": f"Low {i}", "confidence": 0.9}
        for i in range(10)
    ]
    findings.append({
        "severity": "critical",
        "file": "app.py",
        "line": 99,
        "title": "Critical One",
        "confidence": 0.9
    })

    filtered = validate_and_filter_findings(findings, diff)
    assert len(filtered) == 8
    # The critical one must be first
    assert filtered[0]["severity"] == "critical"


def test_load_cached_review_bad_and_fixed():
    """Cached review selector picks bad vs fixed fixture based on diff."""
    bad_diff = Path("demo/fixtures/pr_bad.diff").read_text()
    fixed_diff = Path("demo/fixtures/pr_fixed.diff").read_text()

    bad_review = load_cached_review(bad_diff)
    assert any(f["severity"] == "critical" for f in bad_review["findings"])

    fixed_review = load_cached_review(fixed_diff)
    assert not any(f["severity"] in ("critical", "high") for f in fixed_review["findings"])


@pytest.mark.asyncio
async def test_review_pr_diff_replay_mode():
    """review_pr_diff in replay mode returns cached fixtures with replayed_from_cache=True."""
    bad_diff = Path("demo/fixtures/pr_bad.diff").read_text()
    review = await review_pr_diff(bad_diff, "Test PR")
    assert review.get("replayed_from_cache") is True
    assert len(review.get("findings", [])) >= 2
