"""Pipeline engine: executes the 7-step CI/CD & review pipeline."""
import asyncio
import json
import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from backend.app.db import get_connection
from backend.app.events import event_bus, Event
from backend.app.gate import evaluate_gate
from backend.app.reviewer import review_pr_diff
from backend.app.config import settings

logger = logging.getLogger(__name__)

STEP_NAMES = [
    ("webhook_received", "Webhook received"),
    ("build_and_tests", "Build and tests"),
    ("security_scan", "Security scan"),
    ("ai_review", "AI and rule review"),
    ("quality_gate", "Quality gate"),
    ("staging_deploy", "Staging deploy"),
    ("smoke_tests", "Smoke tests"),
]


def utc_now_iso() -> str:
    """Current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


async def emit_and_save_step(
    run_id: str,
    name: str,
    status: str,
    message: str = "",
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None
) -> Dict[str, Any]:
    """Persist step update to SQLite and publish SSE event."""
    conn = get_connection()
    c = conn.cursor()

    # Check if step exists
    c.execute("SELECT id, started_at FROM steps WHERE run_id = ? AND name = ?", (run_id, name))
    existing = c.fetchone()

    step_id = existing["id"] if existing else str(uuid.uuid4())
    s_at = started_at or (existing["started_at"] if existing else utc_now_iso())
    f_at = finished_at

    if existing:
        c.execute("""
            UPDATE steps
            SET status = ?, message = ?, started_at = ?, finished_at = ?
            WHERE id = ?
        """, (status, message, s_at, f_at, step_id))
    else:
        c.execute("""
            INSERT INTO steps (id, run_id, name, status, message, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (step_id, run_id, name, status, message, s_at, f_at))

    conn.commit()
    conn.close()

    step_data = {
        "id": step_id,
        "run_id": run_id,
        "name": name,
        "status": status,
        "message": message,
        "started_at": s_at,
        "finished_at": f_at,
    }

    await event_bus.publish(Event(
        type="step_updated",
        data=step_data
    ))
    return step_data


async def execute_pipeline(
    run_id: str,
    repo: str,
    pr_number: int,
    pr_title: str,
    author: str,
    head_sha: str,
    mode: str,
    diff: str,
    fast_delays: bool = False,
    github_client: Any = None
) -> Dict[str, Any]:
    """
    Run the 7-step pipeline for a pull request:
    1. Webhook received
    2. Build and tests
    3. Security scan
    4. AI and rule review
    5. Quality gate
    6. Staging deploy (simulated, only if gate passed)
    7. Smoke tests (simulated, only if staging deployed)
    """
    d_step = 0.05 if fast_delays else 0.4
    d_build = 0.1 if fast_delays else 2.0
    d_sec = 0.1 if fast_delays else 1.8
    d_deploy = 0.1 if fast_delays else 2.0
    d_smoke = 0.1 if fast_delays else 1.0

    # Ensure run record exists
    conn = get_connection()
    c = conn.cursor()
    now_iso = utc_now_iso()
    c.execute("SELECT id FROM runs WHERE id = ?", (run_id,))
    if not c.fetchone():
        c.execute("""
            INSERT INTO runs (id, repo, pr_number, pr_title, author, head_sha, mode, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (run_id, repo, pr_number, pr_title, author, head_sha, mode, "running", now_iso))
        conn.commit()
    conn.close()

    await event_bus.publish(Event(
        type="run_created",
        data={
            "run_id": run_id,
            "id": run_id,
            "repo": repo,
            "pr_number": pr_number,
            "pr_title": pr_title,
            "author": author,
            "head_sha": head_sha,
            "mode": mode,
            "status": "running",
            "created_at": now_iso
        }
    ))

    # Initialize all 7 steps as pending
    for name, label in STEP_NAMES:
        await emit_and_save_step(run_id, name, "pending", f"Waiting to run {label.lower()}...")

    # Step 1: Webhook received
    t_start = utc_now_iso()
    await emit_and_save_step(run_id, "webhook_received", "running", "Verifying webhook payload and headers...", started_at=t_start)
    await asyncio.sleep(d_step)
    await emit_and_save_step(
        run_id, "webhook_received", "passed",
        "Webhook signature verified and payload parsed",
        started_at=t_start,
        finished_at=utc_now_iso()
    )

    # Step 2: Build and tests
    t_start = utc_now_iso()
    await emit_and_save_step(run_id, "build_and_tests", "running", "Running pytest suite...", started_at=t_start)
    if mode == "live" and github_client:
        # In LIVE mode, poll check-runs for head SHA up to 60s, skip if none appear within 20s
        check_res = await github_client.poll_check_runs(repo, head_sha, check_name_filter="test", timeout_seconds=60, skip_seconds=20)
        status_s2 = check_res.get("status", "passed")
        msg_s2 = check_res.get("message", "CI tests completed")
    else:
        await asyncio.sleep(d_build)
        status_s2 = "passed"
        msg_s2 = "All tests passed (3/3 unit tests)"

    await emit_and_save_step(
        run_id, "build_and_tests", status_s2,
        msg_s2,
        started_at=t_start,
        finished_at=utc_now_iso()
    )

    # Step 3: Security scan
    t_start = utc_now_iso()
    await emit_and_save_step(run_id, "security_scan", "running", "Running Semgrep security rules...", started_at=t_start)
    if mode == "live" and github_client:
        sec_res = await github_client.poll_check_runs(repo, head_sha, check_name_filter="semgrep", timeout_seconds=60, skip_seconds=20)
        status_s3 = sec_res.get("status", "passed")
        msg_s3 = sec_res.get("message", "Security scan completed")
    else:
        await asyncio.sleep(d_sec)
        # In simulate mode: pass
        status_s3 = "passed"
        msg_s3 = "Semgrep automated scan completed (0 blocking syntax errors)"

    await emit_and_save_step(
        run_id, "security_scan", status_s3,
        msg_s3,
        started_at=t_start,
        finished_at=utc_now_iso()
    )

    # Step 4: AI and rule review
    t_start = utc_now_iso()
    await emit_and_save_step(run_id, "ai_review", "running", f"Analyzing diff with Claude ({settings.claude_model})...", started_at=t_start)

    review_result = await review_pr_diff(diff, pr_title)
    findings = review_result.get("findings", [])
    replayed = review_result.get("replayed_from_cache", False)

    # Persist findings to DB
    conn = get_connection()
    c = conn.cursor()
    for f in findings:
        fid = str(uuid.uuid4())
        c.execute("""
            INSERT INTO findings (id, run_id, severity, file, line, title, explanation, suggested_fix, confidence)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fid, run_id, f.get("severity", "medium"), f.get("file", ""), f.get("line", 0),
            f.get("title", ""), f.get("explanation", ""), f.get("suggested_fix", ""),
            float(f.get("confidence", 1.0))
        ))
        f_record = dict(f)
        f_record["id"] = fid
        f_record["run_id"] = run_id
        await event_bus.publish(Event(
            type="finding_added",
            data={"run_id": run_id, "finding": f_record}
        ))
    conn.commit()
    conn.close()

    cache_note = " (replayed from cache)" if replayed else ""
    review_msg = f"Analysis complete: {len(findings)} finding(s){cache_note}"
    await emit_and_save_step(
        run_id, "ai_review", "passed",
        review_msg,
        started_at=t_start,
        finished_at=utc_now_iso()
    )

    # Step 5: Quality gate
    t_start = utc_now_iso()
    await emit_and_save_step(run_id, "quality_gate", "running", "Evaluating gate policies...", started_at=t_start)
    await asyncio.sleep(d_step)

    # Gather all steps
    steps_dict = {
        "webhook_received": {"status": "passed"},
        "build_and_tests": {"status": status_s2},
        "security_scan": {"status": status_s3},
        "ai_review": {"status": "passed"}
    }
    gate_result = evaluate_gate(steps_dict, findings)

    # Store gate outcome in DB
    reasons_json = json.dumps([{"rule": r.rule, "detail": r.detail} for r in gate_result.reasons])
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE runs
        SET gate_outcome = ?, gate_reasons_json = ?
        WHERE id = ?
    """, (gate_result.outcome, reasons_json, run_id))
    conn.commit()
    conn.close()

    gate_messages = {
        "pass": "Gate open: ship it",
        "warn": f"Gate warnings ({len(gate_result.reasons)} issues flagged)",
        "fail": f"Gate blocked: {gate_result.reasons[0].detail if gate_result.reasons else 'critical issues found'}"
    }
    await emit_and_save_step(
        run_id, "quality_gate", gate_result.outcome,
        gate_messages.get(gate_result.outcome, "Gate evaluated"),
        started_at=t_start,
        finished_at=utc_now_iso()
    )

    await event_bus.publish(Event(
        type="gate_set",
        data={
            "run_id": run_id,
            "outcome": gate_result.outcome,
            "reasons": [{"rule": r.rule, "detail": r.detail} for r in gate_result.reasons]
        }
    ))

    # In LIVE mode, post GitHub commit status and inline PR review comments
    if mode == "live" and github_client:
        try:
            # 1. Post commit status
            status_state = "failure" if gate_result.outcome == "fail" else "success"
            desc = (
                gate_result.reasons[0].detail[:135]
                if gate_result.outcome == "fail" and gate_result.reasons
                else "Quality gate passed: ready to merge"
            )
            target_url = f"{settings.public_base_url}/?run={run_id}"
            await github_client.set_commit_status(
                repo=repo,
                sha=head_sha,
                state=status_state,
                description=desc,
                target_url=target_url,
                context="dev-help-console/gate"
            )

            # 2. Post PR review with inline comments
            await github_client.post_pr_review(
                repo=repo,
                pr_number=pr_number,
                head_sha=head_sha,
                gate_result=gate_result,
                findings=findings,
                diff=diff
            )
        except Exception as gh_err:
            logger.error(f"Error updating GitHub status/review: {gh_err}")
            # Do not crash the application

    # Step 6: Staging deploy (always simulated, only if gate passed)
    t_start = utc_now_iso()
    if gate_result.outcome == "pass":
        await emit_and_save_step(run_id, "staging_deploy", "running", "Deploying artifact to staging environment...", started_at=t_start)
        await asyncio.sleep(d_deploy)
        await emit_and_save_step(
            run_id, "staging_deploy", "passed",
            "Deployed to preview staging environment (internal v1.0.4)",
            started_at=t_start,
            finished_at=utc_now_iso()
        )

        # Step 7: Smoke tests (only runs after staging)
        t_smoke_start = utc_now_iso()
        await emit_and_save_step(run_id, "smoke_tests", "running", "Running automated smoke test suite...", started_at=t_smoke_start)
        await asyncio.sleep(d_smoke)
        await emit_and_save_step(
            run_id, "smoke_tests", "passed",
            "10/10 health checks & smoke tests verified",
            started_at=t_smoke_start,
            finished_at=utc_now_iso()
        )
    else:
        await emit_and_save_step(
            run_id, "staging_deploy", "skipped",
            "Skipped: blocked by quality gate",
            started_at=t_start,
            finished_at=utc_now_iso()
        )
        t_smoke_start = utc_now_iso()
        await emit_and_save_step(
            run_id, "smoke_tests", "skipped",
            "Skipped: staging deploy was not executed",
            started_at=t_smoke_start,
            finished_at=utc_now_iso()
        )

    # Finish run
    fin_time = utc_now_iso()
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE runs
        SET status = 'finished', finished_at = ?
        WHERE id = ?
    """, (fin_time, run_id))
    conn.commit()
    conn.close()

    await event_bus.publish(Event(
        type="run_updated",
        data={
            "run_id": run_id,
            "id": run_id,
            "status": "finished",
            "gate_outcome": gate_result.outcome,
            "finished_at": fin_time
        }
    ))

    return {
        "run_id": run_id,
        "gate_outcome": gate_result.outcome,
        "findings_count": len(findings),
        "status": "finished"
    }
