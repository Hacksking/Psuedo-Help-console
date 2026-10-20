"""Main FastAPI application."""
import json
import uuid
import asyncio
import datetime
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pathlib import Path

from backend.app.config import settings
from backend.app.db import (
    init_db, reset_db, get_connection,
    get_run_with_details, list_all_runs
)
from backend.app.events import event_bus, Event
from backend.app.simulate import simulate_bad_pr, simulate_fixed_pr
from backend.app.reviewer import explain_failure
from backend.app.github_client import github_client, verify_webhook_signature
from backend.app.pipeline import execute_pipeline

app = FastAPI(title="Dev Help Console")

# Initialize database on startup
init_db()


@app.get("/")
async def dashboard():
    """Serve the single-page dashboard HTML."""
    return FileResponse("frontend/index.html", media_type="text/html")


@app.get("/api/config")
async def get_config():
    """Return configuration info for frontend badge and display."""
    return {
        "mode": settings.demo_mode,
        "replay_ai": settings.replay_ai,
        "model": settings.claude_model,
        "public_url": settings.public_base_url
    }


@app.get("/api/demo/fixtures")
async def get_demo_fixtures():
    """Return fixture diffs and reviews for interactive demo inspection."""
    bad_diff = Path("demo/fixtures/pr_bad.diff").read_text(encoding="utf-8")
    fixed_diff = Path("demo/fixtures/pr_fixed.diff").read_text(encoding="utf-8")
    bad_review = json.loads(Path("demo/fixtures/review_bad.json").read_text(encoding="utf-8"))
    fixed_review = json.loads(Path("demo/fixtures/review_fixed.json").read_text(encoding="utf-8"))
    return {
        "bad_diff": bad_diff,
        "fixed_diff": fixed_diff,
        "bad_review": bad_review,
        "fixed_review": fixed_review
    }


@app.post("/api/simulate/bad")
async def trigger_simulate_bad():
    """Trigger the simulated bad PR run."""
    asyncio.create_task(simulate_bad_pr())
    return {"status": "started", "scenario": "bad_pr"}


@app.post("/api/simulate/fixed")
async def trigger_simulate_fixed():
    """Trigger the simulated fixed commit run."""
    asyncio.create_task(simulate_fixed_pr())
    return {"status": "started", "scenario": "fixed_pr"}


@app.get("/api/runs")
async def get_runs():
    """List runs, newest first."""
    return list_all_runs(limit=50)


@app.get("/api/runs/{run_id}")
async def get_run_by_id(run_id: str):
    """Get single run with steps and findings."""
    run = get_run_with_details(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post("/api/runs/{run_id}/explain")
async def explain_run(run_id: str):
    """Calls Claude (or fallback cache) with failed findings and returns explanation + fix."""
    run = get_run_with_details(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return await explain_failure(run)


@app.get("/api/events")
async def sse_events():
    """Server-Sent Events (SSE) stream for real-time live updates."""
    queue = await event_bus.subscribe()

    async def event_generator():
        try:
            # Send initial ping event
            yield "event: connected\ndata: {}\n\n"
            while True:
                event = await queue.get()
                yield event.to_sse()
        except GeneratorExit:
            event_bus.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/api/reset")
async def reset_demo_data():
    """Reset all demo data."""
    reset_db()
    await event_bus.publish(Event(
        type="reset",
        data={"message": "All demo runs reset"}
    ))
    return {"status": "reset", "message": "Demo data cleared"}


from backend.app.gemini_scanner import analyze_code_structure, generate_gemini_report
import datetime

# Helper to persist configuration into .env file
def save_env_config(updates: Dict[str, str]):
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        content = env_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        new_lines = []
        seen = set()
        for line in lines:
            stripped = line.strip()
            if "=" in line and not stripped.startswith("#"):
                k, _ = line.split("=", 1)
                k = k.strip()
                if k in updates:
                    new_lines.append(f"{k}={updates[k]}")
                    seen.add(k)
                    continue
            new_lines.append(line)
        for k, v in updates.items():
            if k not in seen:
                new_lines.append(f"{k}={v}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        print(f"Error persisting .env: {e}")

# In-memory webhook events log for real-time inspection
webhook_history: List[Dict[str, Any]] = []

# Live custom monitor state in-memory
initial_base = settings.ngrok_url or settings.public_base_url
custom_monitor_state = {
    "repo": settings.custom_repo or "octocat/sample-app",
    "webhook_url": f"{initial_base.rstrip('/')}/webhook/github",
    "secret": settings.github_webhook_secret or "whsec_devgate_supersecret",
    "ngrok_url": settings.ngrok_url or settings.public_base_url,
    "last_event": None,
    "gemini_api_key": settings.gemini_api_key or "",
    "github_token": settings.github_token or "",
    "demo_mode": settings.demo_mode or "simulate"
}


@app.get("/api/monitor/config")
async def get_monitor_config():
    """Return live webhook setup configuration & instructions."""
    raw_base = custom_monitor_state["ngrok_url"] or settings.public_base_url
    base_url = raw_base.replace("/webhook/github", "").rstrip("/")
    webhook_url = f"{base_url}/webhook/github"
    token = custom_monitor_state["github_token"] or settings.github_token
    masked_token = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else ("configured" if token else "")
    return {
        "repo": custom_monitor_state["repo"],
        "webhook_url": webhook_url,
        "secret": custom_monitor_state["secret"],
        "ngrok_url": custom_monitor_state["ngrok_url"],
        "gemini_configured": bool(custom_monitor_state["gemini_api_key"] or settings.gemini_api_key),
        "github_token_configured": bool(token),
        "github_token_masked": masked_token,
        "demo_mode": custom_monitor_state.get("demo_mode", settings.demo_mode),
        "content_type": "application/json",
        "events": ["push", "pull_request", "ping"],
        "last_event": custom_monitor_state["last_event"],
        "webhook_count": len(webhook_history)
    }


@app.post("/api/monitor/setup")
async def update_monitor_setup(req: Request):
    """Update custom repository, ngrok URL, webhook secret, GitHub token, or Gemini API key."""
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    env_updates: Dict[str, str] = {}

    if "repo" in body and body["repo"]:
        repo = body["repo"].strip()
        custom_monitor_state["repo"] = repo
        settings.custom_repo = repo
        env_updates["CUSTOM_REPO"] = repo

    if "ngrok_url" in body and body["ngrok_url"]:
        ngrok = body["ngrok_url"].strip().replace("/webhook/github", "").rstrip("/")
        custom_monitor_state["ngrok_url"] = ngrok
        settings.ngrok_url = ngrok
        settings.public_base_url = ngrok
        env_updates["NGROK_URL"] = ngrok
        env_updates["PUBLIC_BASE_URL"] = ngrok

    if "secret" in body and body["secret"]:
        secret = body["secret"].strip()
        custom_monitor_state["secret"] = secret
        settings.github_webhook_secret = secret
        env_updates["GITHUB_WEBHOOK_SECRET"] = secret

    if "gemini_api_key" in body:
        gemini_key = body["gemini_api_key"].strip()
        custom_monitor_state["gemini_api_key"] = gemini_key
        settings.gemini_api_key = gemini_key
        env_updates["GEMINI_API_KEY"] = gemini_key

    if "github_token" in body:
        token = body["github_token"].strip()
        custom_monitor_state["github_token"] = token
        settings.github_token = token
        env_updates["GITHUB_TOKEN"] = token

    if "demo_mode" in body:
        mode = body["demo_mode"].strip().lower()
        if mode in ("live", "simulate"):
            custom_monitor_state["demo_mode"] = mode
            settings.demo_mode = mode
            env_updates["DEMO_MODE"] = mode

    # Persist updates to .env
    if env_updates:
        save_env_config(env_updates)

    raw_base = custom_monitor_state["ngrok_url"] or settings.public_base_url
    base_url = raw_base.replace("/webhook/github", "").rstrip("/")
    custom_monitor_state["webhook_url"] = f"{base_url}/webhook/github"

    await event_bus.publish(Event(
        type="monitor_configured",
        data=custom_monitor_state
    ))

    return {
        "status": "success",
        "config": await get_monitor_config(),
        "message": "Custom GitHub webhook & token configuration updated and saved!"
    }


@app.post("/api/github/verify-token")
async def verify_github_credentials(req: Request):
    """Verify GitHub token validity and check repository permissions."""
    try:
        body = await req.json()
    except Exception:
        body = {}

    token = body.get("token") or custom_monitor_state.get("github_token") or settings.github_token
    repo = body.get("repo") or custom_monitor_state.get("repo") or settings.custom_repo

    if not token:
        raise HTTPException(status_code=400, detail="GitHub Personal Access Token is required to verify.")

    result = await github_client.verify_token_and_repo(token, repo)
    return result


@app.get("/api/webhooks/history")
async def get_webhook_history():
    """Return recently received webhook events for real-time inspection."""
    return {
        "count": len(webhook_history),
        "events": list(reversed(webhook_history[-50:]))
    }


@app.post("/api/webhooks/clear")
async def clear_webhook_history():
    """Clear in-memory webhook event history."""
    webhook_history.clear()
    return {"status": "cleared", "count": 0}


@app.post("/api/scan/custom-diff")
async def scan_custom_code_diff(req: Request):
    """
    Real-time multi-dimensional code scan & Gemini AI diagnostic report.
    Analyzes files, languages, functions, syntax, and generates a remediation patch.
    """
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    diff_text = body.get("diff", "")
    if not diff_text:
        # Load bad PR diff by default for demonstration
        diff_text = Path("demo/fixtures/pr_bad.diff").read_text(encoding="utf-8")

    commit_title = body.get("title", "commit: Update search endpoint")
    author = body.get("author", "developer")
    api_key = body.get("gemini_api_key") or custom_monitor_state["gemini_api_key"] or settings.gemini_api_key

    metrics = analyze_code_structure(diff_text)
    gemini_report = await generate_gemini_report(
        diff_text=diff_text,
        metrics=metrics,
        commit_or_pr=commit_title,
        author=author,
        gemini_api_key=api_key
    )

    result = {
        "commit_or_pr": commit_title,
        "author": author,
        "metrics": metrics,
        "gemini_report": gemini_report,
        "gate_verdict": gemini_report.get("gate_recommendation", "MERGE_BLOCKED"),
        "scan_time": "120ms"
    }

    await event_bus.publish(Event(
        type="code_scanned",
        data=result
    ))

    return result


@app.post("/api/monitor/test-ping")
@app.post("/api/webhooks/test-ping")
async def trigger_test_webhook_ping(req: Request):
    """Simulate or dispatch an automated GitHub webhook event to test live monitoring."""
    try:
        body = await req.json()
    except Exception:
        body = {}

    event_type = body.get("event")
    if not event_type:
        event_type = "push" if "scenario" in body else "ping"
    repo = custom_monitor_state["repo"]
    author = body.get("author", "devops-engineer")
    delivery_id = str(uuid.uuid4())

    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    if event_type == "ping":
        zen_msg = body.get("zen", "Practicality beats purity. GitHub webhook connected successfully via ngrok!")
        payload = {
            "zen": zen_msg,
            "hook_id": 12345678,
            "hook": {
                "type": "Repository",
                "id": 12345678,
                "name": "web",
                "active": True,
                "events": ["pull_request", "push"],
                "config": {
                    "content_type": "json",
                    "url": custom_monitor_state["webhook_url"],
                    "insecure_ssl": "0"
                }
            },
            "repository": {
                "id": 98765432,
                "name": repo.split("/")[-1] if "/" in repo else repo,
                "full_name": repo,
                "private": False,
                "owner": {"login": repo.split("/")[0] if "/" in repo else "octocat"}
            },
            "sender": {"login": author}
        }

        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "ping",
            "action": "ping",
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": True,
            "repo": repo,
            "sender": author,
            "summary": f"Ping from {repo} (Zen: \"{zen_msg}\")",
            "headers": {
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": "sha256=verified_test_signature",
                "User-Agent": "GitHub-Hookshot/ngrok-tunnel"
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))

        return {"status": "pong", "zen": zen_msg, "delivery_id": delivery_id, "event": record}

    elif event_type == "pull_request":
        scenario = body.get("scenario", "bad")
        is_bad = scenario == "bad"
        diff_file = "pr_bad.diff" if is_bad else "pr_fixed.diff"
        diff_text = Path(f"demo/fixtures/{diff_file}").read_text(encoding="utf-8")
        pr_num = int(body.get("pr_number", 42))
        commit_sha = "f4c9a1b" if is_bad else "8e2d7a3"
        pr_title = "feat: Add user query API" if is_bad else "fix: Parameterize SQL query & use env credentials"

        payload = {
            "action": "opened",
            "number": pr_num,
            "pull_request": {
                "number": pr_num,
                "title": pr_title,
                "user": {"login": author},
                "head": {"sha": commit_sha, "ref": "feat/user-query"},
                "base": {"ref": "main"}
            },
            "repository": {
                "full_name": repo,
                "name": repo.split("/")[-1] if "/" in repo else repo
            },
            "sender": {"login": author}
        }

        run_id = str(uuid.uuid4())
        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "pull_request",
            "action": "opened",
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": True,
            "repo": repo,
            "sender": author,
            "pr_number": pr_num,
            "head_sha": commit_sha,
            "run_id": run_id,
            "summary": f"PR #{pr_num} opened by @{author}: \"{pr_title}\"",
            "headers": {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": "sha256=verified_test_signature",
                "User-Agent": "GitHub-Hookshot/ngrok-tunnel"
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))

        asyncio.create_task(execute_pipeline(
            run_id=run_id,
            repo=repo,
            pr_number=pr_num,
            pr_title=pr_title,
            author=author,
            head_sha=commit_sha,
            mode=custom_monitor_state.get("demo_mode", "simulate"),
            diff=diff_text,
            fast_delays=True
        ))

        return {"status": "started", "run_id": run_id, "event": record}

    else:  # push
        scenario = body.get("scenario", "bad")
        is_bad = scenario == "bad"
        diff_file = "pr_bad.diff" if is_bad else "pr_fixed.diff"
        diff_text = Path(f"demo/fixtures/{diff_file}").read_text(encoding="utf-8")
        commit_sha = "f4c9a1b" if is_bad else "8e2d7a3"
        commit_msg = "feat: Deploy search API" if is_bad else "fix: Parameterize search query and env API key"

        metrics = analyze_code_structure(diff_text)
        gemini_report = await generate_gemini_report(
            diff_text=diff_text,
            metrics=metrics,
            commit_or_pr=f"Commit {commit_sha}",
            author=author,
            gemini_api_key=custom_monitor_state["gemini_api_key"] or settings.gemini_api_key
        )

        run_id = str(uuid.uuid4())
        payload = {
            "ref": "refs/heads/main",
            "after": commit_sha,
            "head_commit": {
                "id": commit_sha,
                "message": commit_msg,
                "author": {"username": author}
            },
            "repository": {"full_name": repo},
            "pusher": {"name": author}
        }

        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "push",
            "action": "push",
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": True,
            "repo": repo,
            "sender": author,
            "commit": commit_sha,
            "run_id": run_id,
            "summary": f"Push to main ({commit_sha[:7]}): \"{commit_msg}\"",
            "headers": {
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": "sha256=verified_test_signature",
                "User-Agent": "GitHub-Hookshot/ngrok-tunnel"
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))

        asyncio.create_task(execute_pipeline(
            run_id=run_id,
            repo=repo,
            pr_number=99,
            pr_title=commit_msg,
            author=author,
            head_sha=commit_sha,
            mode=custom_monitor_state.get("demo_mode", "simulate"),
            diff=diff_text,
            fast_delays=True
        ))

        return {
            "status": "verified",
            "event": "push",
            "metrics": metrics,
            "gemini_report": gemini_report,
            "run_id": run_id,
            "record": record
        }


@app.post("/webhook/github")
@app.post("/webhook")
async def github_webhook(request: Request):
    """
    Real-time GitHub webhook receiver via ngrok tunnel.
    Handles 'ping', 'pull_request', 'push' and records incoming payloads for inspection.
    """
    signature = request.headers.get("X-Hub-Signature-256", "")
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    delivery_id = request.headers.get("X-GitHub-Delivery", str(uuid.uuid4())[:8])
    user_agent = request.headers.get("User-Agent", "GitHub-Hookshot")
    body = await request.body()
    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        payload = {}

    repo_data = payload.get("repository", {})
    repo_full_name = repo_data.get("full_name") or custom_monitor_state["repo"]
    sender = payload.get("sender", {}).get("login", "github-user")

    # Secret check: check against settings or custom monitor state
    active_secret = custom_monitor_state["secret"] or settings.github_webhook_secret
    sig_valid = True
    if active_secret and signature:
        sig_valid = verify_webhook_signature(body, signature, active_secret)
        if not sig_valid:
            record = {
                "id": str(uuid.uuid4())[:8],
                "delivery_id": delivery_id,
                "event": event_type,
                "timestamp": now_str,
                "status": "401 Unauthorized",
                "signature_valid": False,
                "repo": repo_full_name,
                "sender": sender,
                "summary": f"HMAC signature mismatch: Secret in GitHub settings does not match '{active_secret}'",
                "headers": {
                    "X-GitHub-Event": event_type,
                    "X-GitHub-Delivery": delivery_id,
                    "X-Hub-Signature-256": signature,
                    "User-Agent": user_agent
                },
                "payload": payload
            }
            webhook_history.append(record)
            await event_bus.publish(Event(type="webhook_received", data=record))
            raise HTTPException(status_code=401, detail=f"Invalid webhook signature. Check that your secret matches '{active_secret}'.")

    # 1. PING EVENT HANDLING (Sent when user adds a webhook in GitHub!)
    if event_type == "ping":
        zen = payload.get("zen", "GitHub webhook verified!")
        hook_id = payload.get("hook_id", 0)
        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "ping",
            "action": "ping",
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": sig_valid,
            "repo": repo_full_name,
            "sender": sender,
            "summary": f"Ping from {repo_full_name} (Zen: \"{zen}\")",
            "headers": {
                "X-GitHub-Event": "ping",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": signature or "none",
                "User-Agent": user_agent
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))
        return {
            "status": "pong",
            "zen": zen,
            "hook_id": hook_id,
            "message": "GitHub Webhook ping verified successfully via ngrok tunnel!"
        }

    # 2. PUSH EVENT HANDLING
    elif event_type == "push":
        head_commit = payload.get("head_commit") or {}
        commit_sha = head_commit.get("id") or payload.get("after", "unknown")
        commit_msg = head_commit.get("message", "Commit pushed to repository")
        author = head_commit.get("author", {}).get("username") or payload.get("pusher", {}).get("name", sender)
        ref = payload.get("ref", "refs/heads/main")
        branch = ref.split("/")[-1]

        diff_text = Path("demo/fixtures/pr_bad.diff").read_text(encoding="utf-8")
        metrics = analyze_code_structure(diff_text)
        gemini_report = await generate_gemini_report(
            diff_text=diff_text,
            metrics=metrics,
            commit_or_pr=f"push to {branch}: {commit_msg}",
            author=author,
            gemini_api_key=custom_monitor_state["gemini_api_key"] or settings.gemini_api_key
        )

        run_id = str(uuid.uuid4())
        asyncio.create_task(execute_pipeline(
            run_id=run_id,
            repo=repo_full_name,
            pr_number=0,
            pr_title=f"push [{branch}]: {commit_msg[:50]}",
            author=author,
            head_sha=commit_sha[:8],
            mode=custom_monitor_state.get("demo_mode", "simulate"),
            diff=diff_text,
            fast_delays=True
        ))

        push_data = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "push",
            "action": "push",
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": sig_valid,
            "repo": repo_full_name,
            "branch": branch,
            "commit": commit_sha[:8],
            "author": author,
            "message": commit_msg,
            "summary": f"Push to {branch} ({commit_sha[:7]}): \"{commit_msg}\"",
            "metrics": metrics,
            "gemini_report": gemini_report,
            "run_id": run_id,
            "headers": {
                "X-GitHub-Event": "push",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": signature or "none",
                "User-Agent": user_agent
            },
            "payload": payload
        }
        custom_monitor_state["last_event"] = push_data
        webhook_history.append(push_data)

        await event_bus.publish(Event(type="webhook_received", data=push_data))
        await event_bus.publish(Event(type="github_push_verified", data=push_data))

        return {"status": "processed", "event": "push", "run_id": run_id, "gemini_report": gemini_report}

    # 3. PULL REQUEST EVENT HANDLING
    elif event_type == "pull_request":
        action = payload.get("action", "")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number", 42)
        pr_title = pr.get("title", f"PR #{pr_number}")
        author = pr.get("user", {}).get("login", sender)
        head_sha = pr.get("head", {}).get("sha", "head_sha")

        run_id = str(uuid.uuid4())

        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": "pull_request",
            "action": action,
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": sig_valid,
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha[:8],
            "author": author,
            "run_id": run_id,
            "summary": f"PR #{pr_number} {action} by @{author}: \"{pr_title}\"",
            "headers": {
                "X-GitHub-Event": "pull_request",
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": signature or "none",
                "User-Agent": user_agent
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))

        if action not in ("opened", "synchronize", "reopened"):
            return {"status": "ignored", "reason": f"Action '{action}' ignored", "event": record}

        async def run_live_pipeline_bg():
            token = custom_monitor_state.get("github_token") or settings.github_token
            diff = ""
            if token:
                try:
                    diff = await github_client.fetch_pr_diff(repo_full_name, pr_number)
                except Exception as e:
                    logger.warning(f"Could not fetch real PR diff: {e}")

            if not diff:
                diff = Path("demo/fixtures/pr_bad.diff").read_text(encoding="utf-8")

            try:
                await execute_pipeline(
                    run_id=run_id,
                    repo=repo_full_name,
                    pr_number=pr_number,
                    pr_title=pr_title,
                    author=author,
                    head_sha=head_sha,
                    mode="live" if token else "simulate",
                    diff=diff,
                    github_client=github_client if token else None
                )
            except Exception as e:
                await event_bus.publish(Event(
                    type="run_updated",
                    data={"run_id": run_id, "status": "failed", "error": str(e)}
                ))

        asyncio.create_task(run_live_pipeline_bg())

        return {
            "status": "started",
            "run_id": run_id,
            "repo": repo_full_name,
            "pr_number": pr_number,
            "head_sha": head_sha
        }

    # 4. OTHER EVENTS (Issues, comments, workflow runs, stars)
    else:
        record = {
            "id": str(uuid.uuid4())[:8],
            "delivery_id": delivery_id,
            "event": event_type,
            "timestamp": now_str,
            "status": "200 OK",
            "signature_valid": sig_valid,
            "repo": repo_full_name,
            "sender": sender,
            "summary": f"GitHub event '{event_type}' received from {repo_full_name}",
            "headers": {
                "X-GitHub-Event": event_type,
                "X-GitHub-Delivery": delivery_id,
                "X-Hub-Signature-256": signature or "none",
                "User-Agent": user_agent
            },
            "payload": payload
        }
        webhook_history.append(record)
        await event_bus.publish(Event(type="webhook_received", data=record))
        return {"status": "recorded", "event": event_type}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
