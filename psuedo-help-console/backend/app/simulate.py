"""Simulate mode: replays fixtures through the real pipeline engine."""
import uuid
from pathlib import Path
from typing import Dict, Any

from backend.app.pipeline import execute_pipeline

FIXTURES_PATH = Path("demo/fixtures")


def load_fixture_text(filename: str) -> str:
    """Read fixture diff text."""
    path = FIXTURES_PATH / filename
    return path.read_text(encoding="utf-8")


async def simulate_bad_pr(fast_delays: bool = False) -> Dict[str, Any]:
    """
    Simulate a pull request with planted security vulnerabilities:
    - SQL injection
    - Hardcoded API key
    """
    run_id = str(uuid.uuid4())
    diff = load_fixture_text("pr_bad.diff")
    return await execute_pipeline(
        run_id=run_id,
        repo="octocat/sample-app",
        pr_number=42,
        pr_title="feat: Add user search endpoint",
        author="alice",
        head_sha="a1b2c3d",
        mode="simulate",
        diff=diff,
        fast_delays=fast_delays
    )


async def simulate_fixed_pr(fast_delays: bool = False) -> Dict[str, Any]:
    """
    Simulate the fix commit for the same PR:
    - Parameterized query
    - Secrets read from environment variable
    """
    run_id = str(uuid.uuid4())
    diff = load_fixture_text("pr_fixed.diff")
    return await execute_pipeline(
        run_id=run_id,
        repo="octocat/sample-app",
        pr_number=42,
        pr_title="fix: Use parameterized query and env var for search API",
        author="alice",
        head_sha="e5f6a7b",
        mode="simulate",
        diff=diff,
        fast_delays=fast_delays
    )
