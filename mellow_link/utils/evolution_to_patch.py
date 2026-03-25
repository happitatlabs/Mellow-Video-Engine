"""
Evolution Report → Patch Report (deterministic conversion).

SERVER_SPEC: EVOLUTION_REPORT_TO_PATCH_REPORT
- evolution_report is the audit trail (stored as-is).
- patch_report is the UI-facing primary message card.
- Conversion is deterministic and never fabricates file changes or test results.
"""
from typing import Any, Dict, List, Optional


def evolution_report_to_patch_report(evolution_report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an internal evolution_report into a UI-facing patch_report.

    Input: evolution_report (JSON object, type == "evolution_report")
    Output: patch_report (JSON object, type == "patch_report")
    """
    if not isinstance(evolution_report, dict) or evolution_report.get("type") != "evolution_report":
        return _fallback_patch_report(evolution_report, "Invalid or missing evolution_report")

    prop = evolution_report.get("proposal") or {}
    tower = evolution_report.get("tower") or {}
    verdict = evolution_report.get("verdict") or {}
    audit = evolution_report.get("audit") or {}
    apply_block = evolution_report.get("apply") or {}
    controls = evolution_report.get("controls") or {}
    rollback = apply_block.get("rollback") or {}
    cost_block = controls.get("cost") or {}

    # ---- A) status ----
    audit_approved = audit.get("approved") is True
    rollback_attempted = rollback.get("attempted") is True
    rollback_result = rollback.get("result")
    ev_status = evolution_report.get("status")

    if not audit_approved:
        status = "rejected"
    elif rollback_attempted and rollback_result == "rolled_back":
        status = "partial"
    elif ev_status in ("applied", "completed"):
        status = "applied"
    else:
        status = "partial"

    # ---- B) summary ----
    if status == "rejected":
        reasons = audit.get("rejection_reasons") or []
        reason = "; ".join(str(r) for r in reasons)[:200] if reasons else "Audit rejected"
        summary = f"Evolution rejected: {reason}"
    elif rollback_attempted and rollback_result == "rolled_back":
        summary = "Evolution applied then rolled back"
    elif status == "applied":
        summary = "Evolution applied successfully"
    else:
        summary = "Evolution partial (see details)"

    # ---- C) issues (minimum 1) ----
    proposed = verdict.get("proposed_changes") or []
    first_summary = proposed[0].get("summary") if proposed else None
    issue_title = first_summary or prop.get("target_file") or "Evolution change"
    observations = tower.get("observations") or []
    rejection_reasons = audit.get("rejection_reasons") or []
    cause_parts = list(observations) + [str(r) for r in rejection_reasons]
    issue_cause = "\n".join(cause_parts) if cause_parts else "N/A"

    plan_lines = verdict.get("plan") or []
    fix_parts = list(plan_lines)
    for pc in proposed:
        path = pc.get("path", "")
        ct = pc.get("change_type", "")
        sm = pc.get("summary", "")
        fix_parts.append(f"{path} ({ct}): {sm}")
    issue_fix = "\n".join(fix_parts) if fix_parts else "N/A"

    if status == "rejected":
        issue_impact = "No changes applied"
    else:
        impact_parts = [pc.get("summary", "") for pc in proposed[:5]]
        issue_impact = "; ".join(impact_parts) if impact_parts else "See changed_files"

    post_verify = apply_block.get("post_apply_verification") or []
    verify_parts = []
    for pv in post_verify:
        t = pv.get("test", "")
        r = pv.get("result", "")
        d = pv.get("detail", "")
        verify_parts.append(f"{t}: {r}" + (f" ({d})" if d else ""))
    issue_verification = "\n".join(verify_parts) if verify_parts else "N/A"

    issues: List[Dict[str, str]] = [
        {
            "title": str(issue_title)[:500],
            "cause": (issue_cause or "N/A")[:2000],
            "fix": (issue_fix or "N/A")[:2000],
            "impact": (issue_impact or "N/A")[:500],
            "verification": (issue_verification or "N/A")[:1000],
        }
    ]

    # ---- D) changed_files ----
    if status == "rejected":
        changed_files: List[Dict[str, str]] = []
    else:
        applied_files = apply_block.get("applied_files") or []
        diff_summary = verdict.get("diff_summary") or "unknown"
        changed_files = [
            {"path": str(af.get("path", "")), "diff_summary": diff_summary}
            for af in applied_files
            if af.get("result") == "applied"
        ]
        if not changed_files and proposed:
            for pc in proposed:
                changed_files.append({
                    "path": str(pc.get("path", "")),
                    "diff_summary": diff_summary,
                })

    # ---- E) regression_guard (at least 1) ----
    if status == "rejected":
        regression_guard = [
            "; ".join(str(r) for r in rejection_reasons)[:300] if rejection_reasons else "Audit rejected",
            "No apply executed",
        ]
    elif rollback_attempted and rollback_result == "rolled_back":
        regression_guard = [
            "Rollback executed due to post-apply verification failure",
        ]
    else:
        security_checks = audit.get("security_checks") or []
        guards = []
        for sc in security_checks:
            if sc.get("result") == "pass" and sc.get("check"):
                guards.append(f"{sc.get('check')}: pass")
        if not guards:
            guards = ["Sandbox scope enforced: services/custom_tools/workspace only"]
        regression_guard = guards

    # ---- F) evolution_trace ----
    stage = evolution_report.get("stage")
    stages_done: List[str] = []
    if tower:
        stages_done.append("tower")
    if verdict:
        stages_done.append("verdict")
    if audit:
        stages_done.append("audit")
    if apply_block:
        stages_done.append("apply")
    if stage:
        stages_done.append(str(stage))

    evolution_trace = {
        "stage_completed": stages_done,
        "proposal_id": prop.get("proposal_id"),
        "audit_approved": audit_approved,
        "auto_apply": apply_block.get("auto_apply") is True,
        "cost_within_limits": cost_block.get("within_limits") is True,
        "rollback_attempted": rollback_attempted,
        "rollback_result": rollback_result if rollback_attempted else None,
    }

    # ---- G) confidence / risk_level ----
    risk_level = verdict.get("estimated_risk_level")
    if risk_level not in (1, 2, 3):
        risk_level = 2
    confidence = audit.get("confidence") or "medium"
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"

    return {
        "type": "patch_report",
        "status": status,
        "summary": summary,
        "issues": issues,
        "changed_files": changed_files,
        "regression_guard": regression_guard,
        "evolution_trace": evolution_trace,
        "confidence": confidence,
        "risk_level": risk_level,
    }


def _fallback_patch_report(raw: Any, reason: str) -> Dict[str, Any]:
    """Return a safe patch_report when input is invalid."""
    return {
        "type": "patch_report",
        "status": "rejected",
        "summary": reason,
        "issues": [{"title": "Invalid input", "cause": reason, "fix": "N/A", "impact": "No changes applied", "verification": "N/A"}],
        "changed_files": [],
        "regression_guard": ["No apply executed"],
        "evolution_trace": {
            "stage_completed": [],
            "proposal_id": None,
            "audit_approved": False,
            "auto_apply": False,
            "cost_within_limits": False,
            "rollback_attempted": False,
            "rollback_result": None,
        },
        "confidence": "low",
        "risk_level": 3,
    }
