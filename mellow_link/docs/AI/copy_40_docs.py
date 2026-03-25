"""
프로젝트 루트에서 실행: python mellow_link/docs/AI/copy_40_docs.py
추천 40개 .md를 mellow_link/docs/AI/ 로 복사 (01_ ~ 40_ 접두사).
"""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]  # AI_Project
DEST = Path(__file__).resolve().parent      # mellow_link/docs/AI

FILES = [
    "AI_PROJECT_STRUCTURE_AND_SPEC.md",
    "SYSTEM_ARCHITECTURE_GUIDEBOOK.md",
    "TROUBLESHOOTING.md",
    "network_info.md",
    "mellow_link/Mellow_Link_Spec.md",
    "mellow_link/docs/system_map.md",
    "mellow_link/docs/TECHNICAL_SPECIFICATION_v1.md",
    "mellow_link/docs/AUTONOMOUS_AGENT_SYSTEM.md",
    "mellow_link/docs/KNOWN_ISSUES.md",
    "mellow_link/docs/EVOLUTION_VERIFICATION_GUIDE.md",
    "mellow_link/docs/EVOLUTION_VERIFICATION.md",
    "mellow_link/docs/MELLOW_LINK_FLOW_MAP.md",
    "mellow_link/docs/MELLOW_LINK_Approval_Gate_Flow_Map.md",
    "mellow_link/docs/Mellow_Link_Storage_Aware_Flow_Map.md",
    "mellow_link/docs/MELLOW_LINK_FEATURES_MINDMAP.md",
    "mellow_link/docs/MELLOW_LINK_Layer System Map.md",
    "mellow_link/docs/OUTPUT_POLICY.md",
    "mellow_link/docs/LONG_FORM_OUTPUT_POLICY.md",
    "mellow_link/docs/PROGRESSIVE_OUTPUT_POLICY.md",
    "mellow_link/docs/MAX_TOKENS_CONTROL.md",
    "mellow_link/docs/CHAT_MODE_FAST_THINKING_RESEARCH.md",
    "mellow_link/docs/TOOL_OUTPUT_LIMITS.md",
    "mellow_link/docs/HOW_TO_TEST_TOOL_USAGE.md",
    "mellow_link/docs/AGENT_UI_ARCHITECTURE_V1.md",
    "mellow_link/docs/DEV_CONSOLE_UX_SPEC.md",
    "mellow_link/docs/PROGRESS_UI.md",
    "mellow_link/docs/VRAM_MANAGEMENT.md",
    "mellow_link/docs/VRAM_OPTIMIZATION.md",
    "mellow_link/docs/VRAM_SELF_KILL.md",
    "mellow_link/docs/PATH_NORMALIZATION_FIX.md",
    "mellow_link/docs/WORKSPACE_CHUNK_AND_EMBED.md",
    "mellow_link/docs/LONG_TEXT_QUEUE_PROCESSING.md",
    "mellow_link/docs/SLM_REACT_LOOP_IMPROVEMENTS.md",
    "mellow_link/docs/SELF_EVOLUTION_GAPS.md",
    "mellow_link/docs/SECURITY_HOTFIX_2026-02-24.md",
    "mellow_link/docs/PERFORMANCE_STABILITY_VALIDATION.md",
    "mellow_link/docs/TTFT_PERFORMANCE_FIX.md",
    "mellow_link/docs/PYTEST_REPRODUCE.md",
    "mellow_link/docs/Retention policy.md",
    "mellow_link/core/tools/README.md",
]

def main():
    DEST.mkdir(parents=True, exist_ok=True)
    for i, rel in enumerate(FILES, 1):
        src = ROOT / rel
        if not src.exists():
            print(f"SKIP (not found): {rel}")
            continue
        name = src.name
        if " " in name:
            name = name.replace(" ", "_")
        dest_name = f"{i:02d}_{name}"
        dest_path = DEST / dest_name
        shutil.copy2(src, dest_path)
        print(f"OK {i:2d}: {dest_name}")
    print(f"\nDone. {DEST} has {len(list(DEST.glob('*.md')))} .md files.")

if __name__ == "__main__":
    main()
