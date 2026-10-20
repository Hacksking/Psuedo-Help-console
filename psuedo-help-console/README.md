# Dev Help Console ⚡

A hackathon demo dashboard that watches pull requests, runs an AI code review using Claude, applies a strict quality gate, and streams every step live via Server-Sent Events (SSE).

Supports two identical-looking modes:
- **SIMULATE mode** (Stage safety net): Runs 100% offline with zero dependencies or credentials needed, replaying local fixtures via the exact same pipeline engine.
- **LIVE mode**: Real GitHub webhook (`pull_request`) + real PR diff fetch + real Anthropic Claude API review + inline review comments on GitHub + commit status (`dev-help-console/gate`) blocking the merge button.

---

## 5-Minute Setup

### 1. Clone & Install Dependencies
Requires Python 3.11+.

```bash
git clone <repo-url> dev-help-console
cd dev-help-console
pip install -r requirements.txt
```

### 2. Configure Environment (.env)
Copy the example config:
```bash
cp .env.example .env
```

For **SIMULATE mode** (default), no API keys are required:
```ini
DEMO_MODE=simulate
REPLAY_AI=true
PUBLIC_BASE_URL=http://localhost:8000
```

For **LIVE mode**, set your keys:
```ini
DEMO_MODE=live
REPLAY_AI=false
ANTHROPIC_API_KEY=sk-ant-api03-...
CLAUDE_MODEL=claude-sonnet-5-5
GITHUB_TOKEN=ghp_...
GITHUB_WEBHOOK_SECRET=your_webhook_secret_here
PUBLIC_BASE_URL=https://xxxx.ngrok-free.app
```

### 3. Launch the App
```bash
uvicorn backend.app.main:app --reload --port 8000
```

Open `http://localhost:8000` in Google Chrome.

---

## LIVE Mode: Exposing with ngrok & GitHub Webhook

To connect Dev Help Console to a real GitHub repository:

### Step 1: Start ngrok
```bash
ngrok http 8000
```
Copy the forwarding HTTPS URL (e.g. `https://xxxx-xx-xx.ngrok-free.app`).

### Step 2: Update `.env`
```ini
PUBLIC_BASE_URL=https://xxxx-xx-xx.ngrok-free.app
DEMO_MODE=live
REPLAY_AI=false
```
Restart uvicorn so the changes take effect.

### Step 3: Configure GitHub Webhook
In your target GitHub repository (or fork of `sample-app`):
1. Navigate to **Settings** → **Webhooks** → **Add webhook**.
2. **Payload URL**: `https://xxxx-xx-xx.ngrok-free.app/webhook/github`
3. **Content type**: `application/json`
4. **Secret**: Paste the exact secret defined in `GITHUB_WEBHOOK_SECRET`.
5. **Which events would you like to trigger this webhook?**:
   - Select **Let me select individual events**.
   - Check **Pull requests** (leave other events unchecked).
6. Click **Add webhook**.

### Step 4: GitHub Personal Access Token (PAT)
Generate a fine-grained GitHub PAT with permissions on the repository:
- **Pull requests**: Read and write (to post review comments)
- **Commit statuses**: Read and write (to set `dev-help-console/gate` check status)
- **Contents**: Read (to read PR diffs)

---

## 3-Minute Stage Demo Script

| Time | Action | What Happens / What to Say |
|---|---|---|
| **0:00 - 0:30** | Intro & Empty State | Show `http://localhost:8000`. Point out the dark terminal interface and live SSE connection. *"Here's Dev Help Console—a live pipeline that protects production using AI code review."* |
| **0:30 - 1:15** | Trigger Bad PR | Click **"🚨 Simulate bad PR"** (or open PR #1 in GitHub). Watch the 7 steps execute live. Webhook verified → Tests pass → AI review runs with Claude → 2 security vulnerabilities detected (SQL Injection + Hardcoded API key). |
| **1:15 - 1:45** | Explain & Inspect Gate | Point out the big RED **"🛑 You shall not pass"** banner. Explain that the GitHub merge button is blocked with status `dev-help-console/gate`. Click **"🤖 Explain this failure"** to show plain-language remediation and click **"📋 Copy fix"** on the findings card. |
| **1:45 - 2:30** | Trigger Fix PR | Click **"✨ Simulate fix"** (or push the fix commit in GitHub). The pipeline re-runs for the same PR. The AI reviewer detects parameterized queries. Staging deploy runs (2s) and Smoke tests pass (1s). |
| **2:30 - 3:00** | Green Gate & Conclusion | The gate turns bright GREEN: **"✅ Gate open: ship it"**. Merge button unblocked. Conclude with how this saves engineering time and prevents security leaks. |

---

## What is Real vs. Simulated

| Pipeline Step | SIMULATE Mode | LIVE Mode |
|---|---|---|
| **1. Webhook received** | Simulated HMAC verification (0.4s) | Real GitHub HMAC-SHA256 signature check (`/webhook/github`) |
| **2. Build and tests** | Simulated pytest delay (2s) | Real check-runs polled from GitHub Actions (up to 60s) |
| **3. Security scan** | Simulated Semgrep delay (1.8s) | Real Semgrep check-run polled from GitHub |
| **4. AI and rule review** | Replayed from fixture (`review_bad.json`) | Real Anthropic Claude API call with diff + Pydantic validation |
| **5. Quality gate** | Real Python gate policy engine | Real Python gate policy + real GitHub commit status & inline PR review |
| **6. Staging deploy** | Simulated deploy (2s, only if gate passed) | Simulated deploy (2s, only if gate passed) |
| **7. Smoke tests** | Simulated health checks (1s) | Simulated health checks (1s) |
| **Inline PR Comments** | Visual findings cards with copyable fix | Real inline comments on changed diff lines on GitHub PR |

---

## Stage-Failure Checklist (Safety Net)

If WiFi drops, GitHub rate limits, or Claude API hiccups mid-pitch:

1. **Switch to SIMULATE**: In the top header or `.env`, set `DEMO_MODE=simulate` and `REPLAY_AI=true`.
2. **Instant Reset**: Click the **"Reset demo"** button in the top right to start with a fresh slate.
3. **Use Stage Demo Controls**: Click **"Simulate bad PR"** and **"Simulate fix"**. It uses the identical pipeline and UI components.
4. **Backup Screen Recording**: A prerecorded demo video is kept in the artifacts directory.
5. **Quick CLI Reset**:
   ```bash
   python -c "from backend.app.db import reset_db; reset_db()"
   ```

---

## Sample App Repository Setup

To test LIVE mode in an isolated repository:
```bash
cd sample-app
bash ../scripts/make_demo_branches.sh
```
This automatically sets up:
- `main`: clean base FastAPI app with tests & CI workflow.
- `feature/user-search`: adds `/users/search` with planted SQL injection and hardcoded API key (unit tests pass!).
- `fix/user-search`: follows up with parameterized queries and environment variable usage.

---

## Running Automated Tests

Run the complete pytest test suite:
```bash
pytest backend/tests/ -v
```

All 20 test cases cover:
- Quality gate policy evaluation (pass, warn, fail, multi-issue formatting)
- AI Reviewer JSON parsing, markdown fence removal, Pydantic validation, diff line checking
- Webhook HMAC-SHA256 signature verification & diff line indexer
- Full end-to-end simulation flow (asserting bad PR -> RED gate, then fixed PR -> GREEN gate)
