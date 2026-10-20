"""End-to-end test of simulate mode verifying bad (RED) and fixed (GREEN) pipeline flows."""
import pytest
from backend.app.db import init_db, reset_db, get_run_with_details, list_all_runs
from backend.app.simulate import simulate_bad_pr, simulate_fixed_pr


@pytest.mark.asyncio
async def test_simulate_bad_then_fixed_e2e():
    """Verify that simulating bad PR results in FAIL (red) and simulating fixed PR results in PASS (green)."""
    init_db()
    reset_db()

    # 1. Run simulated bad PR
    bad_result = await simulate_bad_pr(fast_delays=True)
    bad_run_id = bad_result["run_id"]
    assert bad_result["gate_outcome"] == "fail"

    bad_run = get_run_with_details(bad_run_id)
    assert bad_run is not None
    assert bad_run["gate_outcome"] == "fail"
    assert bad_run["pr_number"] == 42
    assert len(bad_run["findings"]) >= 2

    # Check findings include critical and high
    severities = {f["severity"] for f in bad_run["findings"]}
    assert "critical" in severities or "high" in severities

    # Verify staging and smoke were skipped due to gate failure
    steps_by_name = {s["name"]: s["status"] for s in bad_run["steps"]}
    assert steps_by_name["webhook_received"] == "passed"
    assert steps_by_name["build_and_tests"] == "passed"
    assert steps_by_name["security_scan"] == "passed"
    assert steps_by_name["ai_review"] == "passed"
    assert steps_by_name["quality_gate"] == "fail"
    assert steps_by_name["staging_deploy"] == "skipped"
    assert steps_by_name["smoke_tests"] == "skipped"

    # 2. Run simulated fixed PR (same PR number 42, new commit)
    fixed_result = await simulate_fixed_pr(fast_delays=True)
    fixed_run_id = fixed_result["run_id"]
    assert fixed_result["gate_outcome"] == "pass"

    fixed_run = get_run_with_details(fixed_run_id)
    assert fixed_run is not None
    assert fixed_run["gate_outcome"] == "pass"
    assert fixed_run["pr_number"] == 42

    # Verify no critical/high findings
    for f in fixed_run["findings"]:
        assert f["severity"] not in ("critical", "high")

    # Verify staging and smoke passed because gate passed
    fixed_steps = {s["name"]: s["status"] for s in fixed_run["steps"]}
    assert fixed_steps["quality_gate"] == "pass"
    assert fixed_steps["staging_deploy"] == "passed"
    assert fixed_steps["smoke_tests"] == "passed"

    # Check runs list contains both runs
    runs = list_all_runs()
    assert len(runs) >= 2
    assert runs[0]["id"] == fixed_run_id  # Newest first
    assert runs[1]["id"] == bad_run_id
