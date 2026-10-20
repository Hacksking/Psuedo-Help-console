"""Test Gemini code scanner, multi-dimensional metrics, and live GitHub monitor endpoints."""
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.gemini_scanner import analyze_code_structure, generate_gemini_report

client = TestClient(app)

SAMPLE_BAD_DIFF = """--- a/app.py
+++ b/app.py
@@ -10,10 +10,20 @@
 def list_users():
     return {"users": []}

+@app.get("/users/search")
+def search_users(query: str):
+    API_KEY = "sk-1234567890abcdef"
+    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
+    return {"sql": sql}
"""

SAMPLE_CLEAN_DIFF = """--- a/app.py
+++ b/app.py
@@ -10,10 +10,20 @@
 def list_users():
     return {"users": []}

+@app.get("/users/search")
+def search_users(query: str):
+    import os
+    api_key = os.getenv("API_KEY", "")
+    return {"status": "ok"}
"""


def test_analyze_code_structure_detects_files_and_languages():
    metrics = analyze_code_structure(SAMPLE_BAD_DIFF)
    assert metrics["total_files"] == 1
    assert metrics["files"][0]["file"] == "app.py"
    assert metrics["files"][0]["language"] == "Python"
    assert len(metrics["language_breakdown"]) >= 1
    assert metrics["language_breakdown"][0]["language"] == "Python"


def test_analyze_code_structure_detects_functions():
    metrics = analyze_code_structure(SAMPLE_BAD_DIFF)
    fn_names = [f["name"] for f in metrics["functions_touched"]]
    assert "search_users" in fn_names


def test_analyze_code_structure_flags_security_vulnerabilities():
    metrics = analyze_code_structure(SAMPLE_BAD_DIFF)
    rules = [flg["rule"] for flg in metrics["security_flags"]]
    assert any("SQL Injection" in r for r in rules)
    assert any("Hardcoded Credentials" in r for r in rules)


@pytest.mark.asyncio
async def test_generate_gemini_report_fallback():
    metrics = analyze_code_structure(SAMPLE_BAD_DIFF)
    report = await generate_gemini_report(
        diff_text=SAMPLE_BAD_DIFF,
        metrics=metrics,
        commit_or_pr="PR #42",
        author="alice"
    )
    assert report["risk_level"] in ("Critical", "High")
    assert report["gate_recommendation"] == "MERGE_BLOCKED"
    assert "remediation_patch" in report
    assert "cursor.execute" in report["remediation_patch"]


def test_monitor_config_endpoint():
    res = client.get("/api/monitor/config")
    assert res.status_code == 200
    data = res.json()
    assert "webhook_url" in data
    assert "secret" in data
    assert "content_type" in data
    assert data["content_type"] == "application/json"


def test_monitor_setup_endpoint():
    res = client.post("/api/monitor/setup", json={
        "repo": "myorg/custom-backend",
        "ngrok_url": "https://custom-tunnel.ngrok-free.app",
        "secret": "whsec_custom_test_123"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["config"]["repo"] == "myorg/custom-backend"
    assert "custom-tunnel.ngrok-free.app" in data["config"]["webhook_url"]


def test_scan_custom_diff_endpoint():
    res = client.post("/api/scan/custom-diff", json={
        "diff": SAMPLE_BAD_DIFF,
        "title": "feat: Add user search",
        "author": "alice"
    })
    assert res.status_code == 200
    data = res.json()
    assert "metrics" in data
    assert "gemini_report" in data
    assert data["metrics"]["total_files"] == 1
    assert data["gemini_report"]["gate_recommendation"] == "MERGE_BLOCKED"


def test_monitor_test_ping_endpoint():
    res = client.post("/api/monitor/test-ping", json={
        "scenario": "bad",
        "author": "test-devops"
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "verified"
    assert data["event"] == "push"
    assert "metrics" in data
    assert "gemini_report" in data
