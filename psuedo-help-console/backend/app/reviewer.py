"""Reviewer: Claude-based AI code review and findings parsing."""
import json
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field, ValidationError

import anthropic
from backend.app.config import settings

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path("demo/fixtures")


class FindingModel(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    file: str
    line: int
    title: str
    explanation: str
    suggested_fix: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ReviewResponseModel(BaseModel):
    summary: str
    findings: List[FindingModel] = Field(default_factory=list)


def strip_markdown_fences(text: str) -> str:
    """Robustly extract JSON content from markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        # Remove opening fence
        lines = text.splitlines()
        first_line = lines[0].strip()
        if first_line.startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def parse_review_response(text: str) -> Dict[str, Any]:
    """
    Parse Claude's JSON response robustly:
    - Strips code fences
    - Parses JSON
    - Validates with Pydantic
    """
    clean_text = strip_markdown_fences(text)
    raw_data = json.loads(clean_text)
    validated = ReviewResponseModel.model_validate(raw_data)
    return validated.model_dump()


def validate_and_filter_findings(findings: List[Dict[str, Any]], diff: str) -> List[Dict[str, Any]]:
    """
    Filter and sanitize findings:
    - Drop findings below confidence 0.5
    - Drop findings whose file is not in the diff
    - Cap at 8 findings
    - Sort by severity: critical -> high -> medium -> low
    """
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    valid: List[Dict[str, Any]] = []

    for f in findings:
        conf = f.get("confidence", 1.0)
        if conf < 0.5:
            continue

        file_name = f.get("file", "")
        if not file_name:
            continue

        # File must be present in diff headers or body
        if (
            f"--- a/{file_name}" not in diff
            and f"+++ b/{file_name}" not in diff
            and file_name not in diff
        ):
            continue

        valid.append(f)

    # Sort by severity
    valid.sort(key=lambda item: severity_order.get(item.get("severity", "low"), 4))
    return valid[:8]


def load_cached_review(diff: str) -> Dict[str, Any]:
    """Load cached fixture corresponding to diff."""
    # Determine whether diff is the bad or fixed version
    is_bad = (
        "sk-1234567890abcdef" in diff
        or "'%{query}%'" in diff
        or "VULNERABLE" in diff
        or "pr_bad" in diff
        or ("SELECT * FROM users WHERE name LIKE '%" in diff)
    )
    fixture_name = "review_bad.json" if is_bad else "review_fixed.json"
    fixture_path = FIXTURES_DIR / fixture_name

    try:
        if fixture_path.exists():
            content = fixture_path.read_text(encoding="utf-8")
            data = json.loads(content)
            data["replayed_from_cache"] = True
            return data
    except Exception as e:
        logger.warning(f"Failed to read fixture {fixture_path}: {e}")

    # Fallback default if fixture is missing
    return {
        "summary": "Replayed from cache: Security scan and review findings",
        "findings": [
            {
                "severity": "critical",
                "file": "app.py",
                "line": 14,
                "title": "SQL Injection vulnerability",
                "explanation": "User input directly interpolated into SQL query string.",
                "suggested_fix": "Use parameterized query: cursor.execute(sql, (param,))",
                "confidence": 0.99
            }
        ],
        "replayed_from_cache": True
    }


async def review_pr_diff(diff: str, pr_title: str) -> Dict[str, Any]:
    """
    Call Claude to review a PR diff with safe timeout (30s).
    On any failure or if REPLAY_AI=true, falls back to cached fixture.
    """
    if settings.replay_ai or not settings.anthropic_api_key:
        cached = load_cached_review(diff)
        cached["findings"] = validate_and_filter_findings(cached.get("findings", []), diff)
        return cached

    # Prepare truncated diff (up to 60k chars)
    truncated_note = ""
    safe_diff = diff
    if len(safe_diff) > 60000:
        safe_diff = safe_diff[:60000]
        truncated_note = " (Note: diff was truncated to 60,000 characters)"

    system_prompt = (
        "You are an expert security code reviewer. "
        "Be concise. Only report real issues in the changed lines. "
        "Never invent files or line numbers. Say when unsure. "
        "Prefer fewer high-quality findings. Maximum 8 findings. "
        "You must respond ONLY with a valid JSON object matching the requested schema."
    )

    user_prompt = f"""Review the following pull request diff and return ONLY JSON matching this exact schema:
{{
  "summary": "Brief summary string",
  "findings": [
    {{
      "severity": "critical|high|medium|low",
      "file": "path/to/file.ext",
      "line": 12,
      "title": "Short title",
      "explanation": "Clear explanation",
      "suggested_fix": "Concrete code snippet or advice",
      "confidence": 0.95
    }}
  ]
}}

PR Title: {pr_title}

Diff{truncated_note}:
```
{safe_diff}
```
"""

    try:
        client_kwargs: Dict[str, Any] = {
            "api_key": settings.anthropic_api_key,
            "timeout": 30.0,
        }
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url

        client = anthropic.AsyncAnthropic(**client_kwargs)

        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.claude_model,
                max_tokens=2048,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            ),
            timeout=30.0
        )

        response_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                response_text += block.text

        parsed = parse_review_response(response_text)
        parsed["findings"] = validate_and_filter_findings(parsed.get("findings", []), diff)
        parsed["replayed_from_cache"] = False
        return parsed

    except Exception as exc:
        logger.warning(f"Claude review call failed ({exc}); falling back to cached fixture.")
        fallback = load_cached_review(diff)
        fallback["findings"] = validate_and_filter_findings(fallback.get("findings", []), diff)
        fallback["replayed_from_cache"] = True
        return fallback


async def explain_failure(run: Dict[str, Any]) -> Dict[str, Any]:
    """
    Explain a run's failure or findings in plain language with remediation advice.
    Uses Claude if available, or falls back to clear synthesized advice.
    """
    findings = run.get("findings", [])
    reasons = run.get("gate_reasons", [])

    if not findings and not reasons:
        return {
            "explanation": "No failures detected. The quality gate is passing.",
            "recommended_action": "Safe to merge."
        }

    # If in replay mode or missing key, return structured explanation from findings
    if settings.replay_ai or not settings.anthropic_api_key:
        issues_text = []
        fixes_text = []
        for f in findings:
            sev = f.get("severity", "").upper()
            title = f.get("title", "")
            loc = f"{f.get('file')}:{f.get('line')}"
            issues_text.append(f"• [{sev}] {title} ({loc}): {f.get('explanation')}")
            if f.get("suggested_fix"):
                fixes_text.append(f"Fix for {title}:\n{f.get('suggested_fix')}")

        return {
            "explanation": (
                "The pipeline blocked this pull request due to high-severity security vulnerabilities.\n\n"
                + "\n".join(issues_text)
            ),
            "recommended_action": (
                "Apply the recommended code changes below before merging:\n\n"
                + "\n\n".join(fixes_text)
            ),
            "replayed_from_cache": True
        }

    # Call Claude for real explanation
    try:
        client_kwargs: Dict[str, Any] = {
            "api_key": settings.anthropic_api_key,
            "timeout": 30.0,
        }
        if settings.anthropic_base_url:
            client_kwargs["base_url"] = settings.anthropic_base_url
        client = anthropic.AsyncAnthropic(**client_kwargs)

        prompt = (
            f"PR: {run.get('pr_title')} (PR #{run.get('pr_number')})\n"
            f"Gate reasons: {reasons}\n"
            f"Findings: {json.dumps(findings, indent=2)}\n\n"
            "Explain in 2-3 short, clear sentences why this PR was blocked and what the developer needs to do to fix it."
        )

        response = await asyncio.wait_for(
            client.messages.create(
                model=settings.claude_model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            ),
            timeout=30.0
        )
        explanation_text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return {
            "explanation": explanation_text.strip(),
            "recommended_action": "Review the inline diff suggestions, update the branch, and push a new commit.",
            "replayed_from_cache": False
        }
    except Exception as exc:
        logger.warning(f"Explain failure call failed ({exc}); returning fallback.")
        return {
            "explanation": "Critical security issues were detected that prevent merging this pull request.",
            "recommended_action": "Switch to parameterized queries and move secrets to environment variables.",
            "replayed_from_cache": True
        }
