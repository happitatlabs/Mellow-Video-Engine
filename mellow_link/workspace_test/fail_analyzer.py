"""
fail_analyzer.py - 자가 발전 실패 분석기

FINAL_REJECT 또는 audit_approved=False로 최종 실패한 Evolution 제안을 분석하여
audit_critique, audit_refined 기반 실패 리포트를 생성합니다.

Usage:
    python workspace/fail_analyzer.py
    python workspace/fail_analyzer.py --output reports/fail_analysis.txt

══════════════════════════════════════════════════════════════════════════════
복구 로직 제언: 실패 사유 기반 Evolution 재도전 절차
══════════════════════════════════════════════════════════════════════════════

1. fail_analyzer 실행 → 리포트에서 audit_critique, audit_refined 추출

2. Evolution 재요청 메시지 생성 (자동 또는 수동):
   - 원본: user_request
   - 피드백: audit_critique + audit_refined
   - 예시: "workspace/README.md 수정. 이전 검수 거부 피드백을 반드시 반영하라:
            [검토 의견] {audit_critique}
            [수정 제안] {audit_refined}"

3. Evolution API 호출:
   - POST /evolution/cycle { "user_request": "<위에서 생성한 메시지>" }
   - 또는 텔레그램: /evolution <메시지>

4. (선택) refine-from-proposal API로 특정 제안 기반 재시도:
   - POST /evolution/refine-from-proposal { "proposal_id": "<실패한 제안 ID>" }
   - 기존 run_evolution_refine_cycle는 audit_feedback을 주입하여 새 사이클 실행

5. 자동화 제안: 자율 틱 또는 스케줄러에서
   - fail_analyzer 실행 → FINAL_REJECT 건 감지
   - 해당 proposal_id로 run_evolution_refine_cycle 호출 (최대 1회/일 등 제한 권장)
   - 또는 실패 리포트를 Autonomous Agent에 컨텍스트로 전달하여 evolution 요청 생성
"""

import json
import re
import sys
from datetime import datetime
from pathlib import Path


def _resolve_base() -> Path:
    """mellow_link 루트 경로 반환."""
    here = Path(__file__).resolve()
    # workspace/fail_analyzer.py -> mellow_link/
    return here.parent.parent


def collect_final_reject_ids(log_path: Path) -> list[str]:
    """evolution.log에서 FINAL_REJECT 이벤트의 proposal id 목록 추출."""
    ids = []
    if not log_path.exists():
        return ids
    text = log_path.read_text(encoding="utf-8", errors="replace")
    # [Evolution] FINAL_REJECT id=33eb8673-5ad7-40f4-b1a8-042b97181bb5 attempts=4 (no notify)
    for m in re.finditer(r"FINAL_REJECT\s+id=([a-f0-9\-]{36})", text):
        ids.append(m.group(1))
    return ids


def load_proposal(ledger_dir: Path, proposal_id: str) -> dict | None:
    """제안서 JSON 로드."""
    path = ledger_dir / f"{proposal_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def collect_all_rejected(ledger_dir: Path) -> list[dict]:
    """audit_approved=False인 모든 제안서 수집."""
    rejected = []
    if not ledger_dir.exists():
        return rejected
    for p in ledger_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("audit_approved") is False:
                rejected.append(data)
        except Exception:
            continue
    return rejected


def generate_report(
    proposals: list[dict],
    final_reject_ids: set[str],
    output_path: Path | None = None,
) -> str:
    """실패 리포트 생성. output_path 지정 시 파일로 저장."""
    lines = [
        "# 자가 발전 실패 분석 리포트",
        "",
        f"생성 시각: {datetime.now().isoformat()}",
        f"분석 대상: {len(proposals)}건 (FINAL_REJECT: {len(final_reject_ids)}건)",
        "",
        "---",
        "",
    ]
    for i, p in enumerate(proposals, 1):
        pid = p.get("id", "")
        is_final = pid in final_reject_ids
        lines.append(f"## [{i}] {pid[:8]}... " + ("[FINAL_REJECT]" if is_final else "[검수 거부]"))
        lines.append("")
        lines.append(f"- **원본 요청**: {p.get('user_request', '(없음)')[:300]}")
        lines.append(f"- **대상 파일**: {p.get('verdict_target_file') or '(미지정)'}")
        lines.append(f"- **생성 시각**: {p.get('created_at', '')}")
        if p.get("error"):
            lines.append(f"- **에러**: {p['error']}")
        lines.append("")
        lines.append("### 검토 의견 (audit_critique)")
        lines.append("```")
        lines.append((p.get("audit_critique") or "(없음)")[:2000])
        lines.append("```")
        lines.append("")
        lines.append("### 수정 제안 (audit_refined)")
        lines.append("```")
        lines.append((p.get("audit_refined") or "(없음)")[:1500])
        lines.append("```")
        lines.append("")
        lines.append("---")
        lines.append("")

    report = "\n".join(lines)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    return report


def main() -> int:
    base = _resolve_base()
    logs_dir = base / "logs"
    evolution_log = logs_dir / "evolution.log"
    proposals_dir = logs_dir / "evolution_proposals"

    final_ids = set(collect_final_reject_ids(evolution_log))
    rejected = collect_all_rejected(proposals_dir)
    # created_at 기준 내림차순
    rejected.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    output_path = None
    if "--output" in sys.argv:
        idx = sys.argv.index("--output")
        if idx + 1 < len(sys.argv):
            out_arg = sys.argv[idx + 1]
            output_path = Path(out_arg) if Path(out_arg).is_absolute() else base / out_arg

    report = generate_report(rejected, final_ids, output_path)
    print(report)
    if output_path:
        print(f"\n[OK] 리포트 저장됨: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
