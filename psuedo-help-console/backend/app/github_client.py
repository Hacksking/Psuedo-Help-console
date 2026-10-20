"""GitHub integration: webhook verification, diff fetching, inline reviews, status checks."""
import hmac
import hashlib
import json
import logging
import re
import asyncio
from typing import Dict, Any, List, Optional
import httpx

from backend.app.config import settings

logger = logging.getLogger(__name__)


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook X-Hub-Signature-256 HMAC."""
    if not secret:
        logger.warning("GITHUB_WEBHOOK_SECRET is not configured.")
        return False
    if not signature or not signature.startswith("sha256="):
        return False

    expected_sig = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_sig)


def parse_diff_lines(diff: str) -> Dict[str, set]:
    """
    Parse unified diff to extract modified line numbers (right-side/new file) per file.
    Returns a dict mapping filename -> set of valid line numbers.
    """
    valid_lines: Dict[str, set] = {}
    current_file = None
    current_line = 0

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:].strip()
            if current_file not in valid_lines:
                valid_lines[current_file] = set()
        elif line.startswith("@@"):
            # Format: @@ -start,count +start,count @@
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                current_line = int(m.group(1))
        elif current_file is not None:
            if line.startswith("+"):
                valid_lines[current_file].add(current_line)
                current_line += 1
            elif line.startswith(" "):
                valid_lines[current_file].add(current_line)
                current_line += 1
            elif line.startswith("-"):
                # Deleted line from old file, doesn't increment new file line
                pass

    return valid_lines


class GitHubClient:
    """Client for GitHub API operations in LIVE mode."""

    def __init__(self):
        self.base_url = "https://api.github.com"

    def _headers(self, accept: str = "application/vnd.github.v3+json") -> Dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": "DevHelpConsole-Reviewer",
        }
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"
        return headers

    async def fetch_pr_diff(self, repo: str, pr_number: int) -> str:
        """Fetch unified diff for a pull request."""
        url = f"{self.base_url}/repos/{repo}/pulls/{pr_number}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.get(url, headers=self._headers("application/vnd.github.v3.diff"))
                if res.status_code == 200:
                    return res.text
                logger.error(f"Failed to fetch PR diff: {res.status_code} - {res.text}")
                return ""
        except Exception as e:
            logger.error(f"Error fetching PR diff: {e}")
            return ""

    async def set_commit_status(
        self,
        repo: str,
        sha: str,
        state: str,
        description: str,
        target_url: str,
        context: str = "dev-help-console/gate"
    ) -> bool:
        """
        Set commit status (success or failure) on GitHub commit.
        """
        if not settings.github_token:
            logger.info(f"Skipping GitHub commit status (no token): state={state}, desc={description}")
            return False

        url = f"{self.base_url}/repos/{repo}/statuses/{sha}"
        payload = {
            "state": state,  # "success", "failure", "pending", "error"
            "description": description[:140],
            "target_url": target_url,
            "context": context
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, headers=self._headers(), json=payload)
                return res.status_code == 201
        except Exception as e:
            logger.error(f"Error setting commit status: {e}")
            return False

    async def poll_check_runs(
        self,
        repo: str,
        head_sha: str,
        check_name_filter: str = "",
        timeout_seconds: int = 60,
        skip_seconds: int = 20
    ) -> Dict[str, Any]:
        """
        Poll GitHub check-runs for the head SHA up to timeout_seconds.
        If none appear within skip_seconds, mark 'skipped'.
        """
        if not settings.github_token:
            return {"status": "skipped", "message": "No GitHub token configured for check-runs polling"}

        url = f"{self.base_url}/repos/{repo}/commits/{head_sha}/check-runs"
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout_seconds:
                return {"status": "warn", "message": f"Check-runs timed out after {timeout_seconds}s"}

            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.get(url, headers=self._headers())
                    if res.status_code == 200:
                        data = res.json()
                        check_runs = data.get("check_runs", [])
                        if check_name_filter:
                            check_runs = [
                                cr for cr in check_runs
                                if check_name_filter.lower() in cr.get("name", "").lower()
                            ]

                        if not check_runs:
                            if elapsed >= skip_seconds:
                                return {"status": "skipped", "message": f"No check-runs detected within {skip_seconds}s (skipped)"}
                        else:
                            # Check status of runs
                            all_completed = all(cr.get("status") == "completed" for cr in check_runs)
                            if all_completed:
                                any_failed = any(cr.get("conclusion") in ("failure", "timed_out", "cancelled") for cr in check_runs)
                                if any_failed:
                                    return {"status": "failed", "message": "One or more check-runs failed"}
                                return {"status": "passed", "message": f"All {len(check_runs)} check-runs passed"}
            except Exception as e:
                logger.warning(f"Error polling check-runs: {e}")

            await asyncio.sleep(3.0)

    async def post_pr_review(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        gate_result: Any,
        findings: List[Dict[str, Any]],
        diff: str
    ) -> bool:
        """
        Post findings as ONE pull request review (event COMMENT) with inline comments.
        Findings whose lines cannot be placed inline go into the review summary body.
        """
        if not settings.github_token:
            logger.info("Skipping GitHub review post: no GITHUB_TOKEN configured")
            return False

        valid_lines_by_file = parse_diff_lines(diff)

        inline_comments = []
        body_comments = []

        for f in findings:
            file_name = f.get("file", "")
            line_no = f.get("line", 0)
            sev = f.get("severity", "medium").upper()
            title = f.get("title", "")
            explanation = f.get("explanation", "")
            fix = f.get("suggested_fix", "")

            comment_text = (
                f"### 🛡️ [{sev}] {title}\n\n"
                f"{explanation}\n\n"
                + (f"**Suggested fix:**\n```python\n{fix}\n```" if fix else "")
            )

            valid_lines = valid_lines_by_file.get(file_name, set())
            if file_name in valid_lines_by_file and (line_no in valid_lines or len(valid_lines) > 0):
                # Target the exact line or nearest valid line in diff
                target_line = line_no if line_no in valid_lines else min(valid_lines, key=lambda l: abs(l - line_no))
                inline_comments.append({
                    "path": file_name,
                    "line": target_line,
                    "side": "RIGHT",
                    "body": comment_text
                })
            else:
                body_comments.append(f"- **[{sev}] {title}** ({file_name}:{line_no})\n  {explanation}")

        # Construct overall review body
        outcome_emoji = "✅" if gate_result.outcome == "pass" else ("⚠️" if gate_result.outcome == "warn" else "🚫")
        body_parts = [
            f"## {outcome_emoji} Dev Help Console Quality Gate: {gate_result.outcome.upper()}",
            ""
        ]

        if gate_result.reasons:
            body_parts.append("### Gate Details:")
            for r in gate_result.reasons:
                body_parts.append(f"- {r.detail}")
            body_parts.append("")

        if body_comments:
            body_parts.append("### Additional Findings:")
            body_parts.extend(body_comments)
            body_parts.append("")

        body_parts.append(f"Dashboard Run Details: {settings.public_base_url}/")

        payload = {
            "commit_id": head_sha,
            "body": "\n".join(body_parts),
            "event": "COMMENT",
            "comments": inline_comments
        }

        url = f"{self.base_url}/repos/{repo}/pulls/{pr_number}/reviews"
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(url, headers=self._headers(), json=payload)
                if res.status_code in (200, 201):
                    logger.info("Successfully posted GitHub PR review.")
                    return True
                logger.error(f"Failed to post GitHub review: {res.status_code} - {res.text}")
                return False
        except Exception as e:
            logger.error(f"Error posting PR review: {e}")
            return False

    async def verify_token_and_repo(self, token: str, repo: str = "") -> Dict[str, Any]:
        """Verify GitHub token validity and check repository access/permissions."""
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DevGate-Console",
            "Authorization": f"Bearer {token.strip()}"
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                user_res = await client.get(f"{self.base_url}/user", headers=headers)
                if user_res.status_code != 200:
                    return {
                        "valid": False,
                        "error": f"Invalid token or unauthorized (HTTP {user_res.status_code})"
                    }
                user_data = user_res.json()
                raw_scopes = user_res.headers.get("x-oauth-scopes", "")
                scopes = [s.strip() for s in raw_scopes.split(",") if s.strip()]

                result: Dict[str, Any] = {
                    "valid": True,
                    "username": user_data.get("login"),
                    "name": user_data.get("name") or user_data.get("login"),
                    "avatar_url": user_data.get("avatar_url"),
                    "scopes": scopes,
                    "repo": repo,
                    "repo_accessible": False,
                    "permissions": {}
                }

                if repo and "/" in repo:
                    repo_res = await client.get(f"{self.base_url}/repos/{repo.strip()}", headers=headers)
                    if repo_res.status_code == 200:
                        repo_data = repo_res.json()
                        result["repo_accessible"] = True
                        result["repo_private"] = repo_data.get("private", False)
                        result["permissions"] = repo_data.get("permissions", {})
                    else:
                        result["repo_error"] = f"Repository '{repo}' not found or no read access (HTTP {repo_res.status_code})"

                return result
        except Exception as e:
            logger.error(f"Error verifying GitHub token: {e}")
            return {"valid": False, "error": str(e)}


github_client = GitHubClient()

