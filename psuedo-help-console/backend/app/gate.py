"""Gate logic: pure function determining whether a PR can merge."""
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Union


@dataclass
class GateReason:
    rule: str
    detail: str


@dataclass
class GateResult:
    outcome: str  # "pass", "warn", "fail"
    reasons: List[GateReason]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reasons": [asdict(r) for r in self.reasons]
        }


def evaluate_gate(
    steps: Union[Dict[str, Any], List[Dict[str, Any]]],
    findings: List[Dict[str, Any]]
) -> GateResult:
    """
    Evaluate quality gate. Pure function.

    Rules:
    - FAIL if:
      - Any required step failed (e.g., build_and_tests, security_scan)
      - Any critical or high severity finding exists
    - WARN if:
      - No failures, but medium severity findings exist
    - PASS otherwise (low findings are shown but do not block the gate)

    Returns: GateResult with outcome and list of GateReason({rule, detail}).
    """
    reasons: List[GateReason] = []

    # Normalize steps to a dict mapping name -> dict
    step_dict: Dict[str, Dict[str, Any]] = {}
    if isinstance(steps, list):
        for s in steps:
            name = s.get("name")
            if name:
                step_dict[name] = s
    elif isinstance(steps, dict):
        step_dict = steps

    # Check required steps
    required_step_names = ["build_and_tests", "security_scan"]
    for req in required_step_names:
        step_info = step_dict.get(req)
        if step_info and isinstance(step_info, dict):
            if step_info.get("status") == "failed":
                msg = step_info.get("message") or f"Step '{req}' failed"
                reasons.append(GateReason(
                    rule="required_step_failed",
                    detail=f"Required step '{req}' failed: {msg}"
                ))

    # Also check if any step generally has status 'failed'
    for name, step_info in step_dict.items():
        if name not in required_step_names and isinstance(step_info, dict) and step_info.get("status") == "failed":
            # If AI review failed completely and produced a failed status
            reasons.append(GateReason(
                rule="step_failed",
                detail=f"Step '{name}' failed"
            ))

    # Check for critical and high findings
    critical_and_high = [
        f for f in findings
        if f.get("severity") in ("critical", "high")
    ]
    if critical_and_high:
        parts = []
        for f in critical_and_high:
            sev = f.get("severity", "high")
            title = f.get("title") or "finding"
            file = f.get("file", "unknown")
            line = f.get("line", 0)
            parts.append(f"{title} in {file}:{line} ({sev})")

        reasons.append(GateReason(
            rule="high_severity_findings",
            detail=f"{len(critical_and_high)} critical/high finding(s): {', '.join(parts)}"
        ))

    if reasons:
        return GateResult(outcome="fail", reasons=reasons)

    # Check for medium findings
    medium_findings = [f for f in findings if f.get("severity") == "medium"]
    if medium_findings:
        parts = [
            f"{f.get('title') or 'finding'} in {f.get('file', 'unknown')}:{f.get('line', 0)}"
            for f in medium_findings
        ]
        return GateResult(
            outcome="warn",
            reasons=[GateReason(
                rule="medium_findings",
                detail=f"{len(medium_findings)} medium finding(s): {', '.join(parts)}"
            )]
        )

    return GateResult(outcome="pass", reasons=[])
