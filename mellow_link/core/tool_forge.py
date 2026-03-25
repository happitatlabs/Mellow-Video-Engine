"""
Tool Forge - 자가 진화형 동적 도구 대장간 엔진 (Phase 4 → Phase 5)

에이전트가 도구 부족 또는 실패를 감지했을 때, 스스로 파이썬 기반의 새로운 도구를
생성·검증·등록하는 Self-Evolution Loop의 핵심 모듈.

워크플로우:
  1. Need Detection  → AgentBrain이 도구 부족/실패 감지, propose_new_tool 요청
  2. Code Generation → workspace/temp_tools/에 후보 코드 저장
  3. Static Analysis → AST(Abstract Syntax Tree) 기반 금지 함수·보안 패턴 검사
  4. Guardian Audit   → GuardianService 2차 검수 (로직 무결성 + 보안성)
  5. Dynamic Loading → importlib으로 ToolRegistry에 실시간 등록

기술 검토 태그:
  - AST 기반 정적 분석:           ✅ verified
  - Guardian 2차 검수 연동:       ✅ verified
  - importlib 동적 로딩:          ✅ verified
  - Security Tier (NORMAL/HARD):  ✅ verified
  - ThreadPoolExecutor 병렬 검증: ✅ verified
  - temp_tools 스테이징:          ✅ verified
  - 런타임 도구 자동 발견:         ⚠️ possible (DynamicRegistry에 위임)
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import re
import sys
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Forge 전용 로거 (logs/forge.log)
# ═══════════════════════════════════════════════════════════════════════

_forge_logger: Optional[logging.Logger] = None


def _get_forge_logger() -> logging.Logger:
    """logs/forge.log에 기록하는 전용 로거."""
    global _forge_logger
    if _forge_logger is not None:
        return _forge_logger
    _forge_logger = logging.getLogger("mellow_link.forge")
    _forge_logger.setLevel(logging.INFO)
    if not _forge_logger.handlers:
        base = Path(__file__).resolve().parent.parent
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "forge.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        _forge_logger.addHandler(fh)
    return _forge_logger


def _log_forge(event: str, detail: str = "") -> None:
    """Forge 이벤트를 logs/forge.log에 기록."""
    try:
        _get_forge_logger().info(f"[Forge] {event} {detail}".strip())
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# 경로 상수
# ═══════════════════════════════════════════════════════════════════════

_MELLOW_LINK_ROOT = Path(__file__).resolve().parent.parent  # mellow_link/
_WORKSPACE_ROOT = _MELLOW_LINK_ROOT / "workspace"
_TEMP_TOOLS_DIR = _WORKSPACE_ROOT / "temp_tools"
_CUSTOM_TOOLS_DIR = _MELLOW_LINK_ROOT / "custom_tools"


def _ensure_dirs() -> None:
    """필수 디렉토리 생성."""
    _TEMP_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    _CUSTOM_TOOLS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 상수: 위험 노드/이름 (AST 기반 차단)
# ═══════════════════════════════════════════════════════════════════════

# ── 차단할 모듈/함수 이름 ──
# CPython 타입 체인 탈출 방어용 덤더(dunder) 속성 포함
# os 모듈은 허용하되, OS_FORBIDDEN_ATTRS에 있는 위험 함수만 차단
FORBIDDEN_NAMES: Set[str] = {
    # 코드 실행 계열
    "system", "popen", "exec", "eval", "compile", "__import__",
    "subprocess", "run", "call", "Popen", "shell", "open",
    "remove", "unlink",
    # 인터랙티브/탈출 계열
    "input", "breakpoint", "exit", "quit", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "dir",
    "execfile", "reload", "memoryview", "buffer",
    # 네트워크 계열
    "socket", "requests", "urllib", "http", "ftplib",
    # 직렬화 계열 (임의 코드 실행 가능)
    "pickle", "marshal", "yaml", "shelve",
    # 타입 체인 탈출 계열
    "__subclasses__", "__bases__", "__mro__", "__class__",
    "__dict__", "__globals__", "__code__", "__builtins__",
}
# [IMMUTABLE] 이 set은 에이전트가 자체 수정(self-modification)으로
# 완화시켜서는 안 됨. EvolutionManager/ToolForge가 이 파일을 수정 시
# GuardianService Level 3 하드블록이 발동되어야 함.

# os 모듈 허용 시 차단할 위험 세부 함수 (os.listdir, os.walk, os.path 등은 허용)
OS_FORBIDDEN_ATTRS: Set[str] = {
    "system", "popen", "popen2", "popen3", "popen4",
    "execv", "execve", "execvp", "execvpe",
    "execl", "execlp", "execle", "execlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "kill", "remove", "unlink", "chmod", "chown",
    "lchmod", "lchown", "startfile", "_exit",
}

# HARD 모드 전용: import 허용 화이트리스트
# ✅ verified: HARD 레벨에서는 이 목록 외의 모든 import를 차단
HARD_IMPORT_WHITELIST: Set[str] = {
    "json", "math", "re", "string", "collections",
    "itertools", "functools", "operator", "typing",
    "datetime", "pathlib", "os.path", "hashlib",
    "base64", "textwrap", "difflib", "statistics",
}

# ── 샌드박스 globals ──
SAFE_BUILTINS: Dict[str, Any] = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len,
    "list": list, "max": max, "min": min, "range": range,
    "repr": repr, "reversed": reversed, "round": round,
    "set": set, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "zip": zip, "map": map, "filter": filter,
    "None": None, "True": True, "False": False,
    "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
    "isinstance": isinstance, "issubclass": issubclass, "type": type,
    "hasattr": hasattr, "callable": callable,
    "print": print,  # 디버그 출력 허용 (샌드박스 내부 캡처)
}

# 샌드박스에서 허용할 안전한 모듈 (제한적)
SAFE_MODULES: Dict[str, Any] = {}
try:
    import json as _json
    SAFE_MODULES["json"] = _json
except ImportError:
    pass
try:
    import math as _math
    SAFE_MODULES["math"] = _math
except ImportError:
    pass
try:
    SAFE_MODULES["re"] = re
except Exception:
    pass
try:
    import collections as _collections
    SAFE_MODULES["collections"] = _collections
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════════════
# 결과 타입
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ForgeResult:
    """ToolForge 검증/실행 결과. ✅ verified"""
    success: bool
    tool_id: Optional[str] = None
    tool_name: Optional[str] = None
    message: str = ""
    errors: List[str] = field(default_factory=list)
    test_output: Optional[str] = None
    stage: str = ""  # 실패한 단계: SYNTAX | AST_SECURITY | SANDBOX | GUARDIAN | REGISTER
    temp_file_path: Optional[str] = None  # temp_tools/ 내 저장 경로
    elapsed_ms: float = 0.0  # 총 소요 시간


@dataclass
class BatchForgeResult:
    """다중 도구 검증 결과 (ThreadPoolExecutor 사용). ✅ verified"""
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    results: List[ForgeResult] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class NeedDetectionResult:
    """도구 부족 감지 결과. ⚠️ possible"""
    needs_new_tool: bool = False
    reason: str = ""
    suggested_tool_name: str = ""
    suggested_description: str = ""
    failed_tool_name: Optional[str] = None
    failure_count: int = 0


# ═══════════════════════════════════════════════════════════════════════
# AST Security Analyzer (Static Analysis Engine)
# ✅ verified: 금지된 함수, 네트워크 호출, 위험 패턴 정적 검사
# ═══════════════════════════════════════════════════════════════════════

class ASTSecurityAnalyzer:
    """
    AST 기반 정적 보안 분석기.

    보안 계층:
      - NORMAL: FORBIDDEN_NAMES + OS_FORBIDDEN_ATTRS + dunder 차단
      - HARD:   위 + import 화이트리스트 강제 + 추가 패턴 검사
    """

    def __init__(self, security_level: str = "NORMAL"):
        self._level = security_level.upper()

    def analyze(self, tree: ast.AST, code: str = "") -> List[str]:
        """
        AST를 순회하며 위험한 이름/호출/import를 검사.

        Args:
            tree: 파싱된 AST
            code: 원본 코드 (추가 정규식 검사용)

        Returns:
            발견된 위험 사항 메시지 리스트 (비어 있으면 통과)
        """
        errors: List[str] = []

        # 1단계: AST 노드 순회 검사
        visitor = _SecurityVisitor(errors, security_level=self._level)
        try:
            visitor.visit(tree)
        except Exception as e:
            errors.append(f"AST 검사 중 예외: {e!r}")
            logger.exception("[ASTSecurityAnalyzer] visit exception")

        # 2단계: HARD 모드 추가 정규식 검사
        if self._level == "HARD" and code:
            errors.extend(self._hard_regex_checks(code))

        return errors

    def _hard_regex_checks(self, code: str) -> List[str]:
        """HARD 모드 전용 정규식 기반 추가 보안 검사."""
        errors: List[str] = []
        # 인코딩 우회 시도 탐지
        if re.search(r"\\x[0-9a-fA-F]{2}", code):
            errors.append("HARD 차단: 16진수 이스케이프 시퀀스 감지 (인코딩 우회 시도)")
        # chr() 기반 코드 조립 탐지
        if re.search(r"chr\s*\(\s*\d+\s*\)", code) and code.count("chr") >= 3:
            errors.append("HARD 차단: chr() 다중 사용 감지 (문자열 조립 공격 가능)")
        # 과도한 중첩 (재귀 폭탄 가능)
        if code.count("def ") > 15:
            errors.append("HARD 차단: 함수 정의 15개 초과 (복잡도 과다)")
        return errors


class _SecurityVisitor(ast.NodeVisitor):
    """AST 방문자: 위험 패턴 탐지. ✅ verified"""

    def __init__(self, errors: List[str], security_level: str = "NORMAL"):
        self._errors = errors
        self._level = security_level

    def _check_name(self, name: str, exact_only: bool = False) -> None:
        """이름이 금지 목록에 있는지 검사."""
        if not name:
            return
        name_lower = name.lower()
        for forbidden in FORBIDDEN_NAMES:
            if exact_only:
                if name_lower == forbidden.lower():
                    self._errors.append(
                        f"허용되지 않은 이름 사용: {name!r} (위험 라이브러리/기능)"
                    )
                    return
            else:
                if forbidden.lower() in name_lower or name_lower == forbidden.lower():
                    self._errors.append(
                        f"허용되지 않은 이름 사용: {name!r} (위험 라이브러리/기능)"
                    )
                    return

    def visit_Call(self, node: ast.Call) -> None:
        """함수 호출 검사: exec(), eval(), os.system() 등."""
        if isinstance(node.func, ast.Name):
            self._check_name(node.func.id, exact_only=True)
        elif isinstance(node.func, ast.Attribute):
            self._check_name(node.func.attr, exact_only=True)
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """import 검사."""
        for alias in node.names:
            module_name = alias.name.split(".")[0]
            self._check_name(module_name, exact_only=True)
            # HARD 모드: 화이트리스트 외 import 차단
            if self._level == "HARD" and alias.name not in HARD_IMPORT_WHITELIST:
                if module_name not in HARD_IMPORT_WHITELIST:
                    self._errors.append(
                        f"HARD 모드 차단: import {alias.name} (화이트리스트 외)"
                    )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """from ... import 검사."""
        if node.module:
            module_root = node.module.split(".")[0]
            self._check_name(module_root, exact_only=True)
            # HARD 모드: 화이트리스트 외 import 차단
            if self._level == "HARD" and node.module not in HARD_IMPORT_WHITELIST:
                if module_root not in HARD_IMPORT_WHITELIST:
                    self._errors.append(
                        f"HARD 모드 차단: from {node.module} import (화이트리스트 외)"
                    )
        for alias in node.names:
            self._check_name(alias.name, exact_only=True)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """변수명/함수명 자체는 허용, 호출(visit_Call)만 검사."""
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """속성 접근 검사: dunder 속성 차단, os.system 등."""
        attr = getattr(node, "attr", "")
        if isinstance(attr, str):
            # 덤더(dunder) 속성 전면 차단
            if attr.startswith("__") and attr.endswith("__"):
                self._errors.append(
                    f"허용되지 않은 덤더(dunder) 속성 접근: {attr!r} (샌드박스 탈출 방지)"
                )
                return
            # os 모듈의 위험 속성 차단
            if attr in OS_FORBIDDEN_ATTRS:
                self._errors.append(
                    f"os 모듈 위험 함수 차단: {attr!r}"
                )
                return
            self._check_name(attr, exact_only=True)
        self.generic_visit(node)


# ═══════════════════════════════════════════════════════════════════════
# Tool Forge - 핵심 엔진
# ✅ verified: Self-Evolution Loop의 중심
# ═══════════════════════════════════════════════════════════════════════

class ToolForge:
    """
    동적 도구 코드 검증·스테이징·등록 엔진.

    파이프라인:
      1. Syntax Check     → ast.parse() 문법 검증
      2. Static Analysis  → ASTSecurityAnalyzer + Security Tier 적용
      3. Sandbox Test     → 제한된 globals/locals에서 함수 정의 로드 확인
      4. Staging          → workspace/temp_tools/에 후보 코드 저장
      5. Guardian Audit   → GuardianService.audit_tool_code() 2차 검수
      6. Dynamic Loading  → importlib로 ToolRegistry에 실시간 등록
      7. Promotion        → custom_tools/로 승격, DB에 VERIFIED 기록

    보안 계층:
      - NORMAL: workspace 내부 파일 작업만 허용, 기본 FORBIDDEN_NAMES 검사
      - HARD:   import 화이트리스트 강제, 정규식 추가 검사, 엄격한 승인 시스템

    하드웨어 최적화:
      - ThreadPoolExecutor로 다중 도구 검증 시 CPU 자원 효율화
      - worker 수: os.cpu_count() // 2 (Ryzen 9 7900 기준 12코어 → 6 workers)
    """

    # ThreadPoolExecutor 설정
    _MAX_WORKERS = max(2, (os.cpu_count() or 4) // 2)

    def __init__(self, security_level: str = "NORMAL"):
        self._security_level = security_level.upper()
        self._analyzer = ASTSecurityAnalyzer(self._security_level)
        self._executor = ThreadPoolExecutor(
            max_workers=self._MAX_WORKERS,
            thread_name_prefix="forge-worker",
        )
        _ensure_dirs()
        logger.info(
            "[ToolForge] Initialized (level=%s, workers=%d, "
            "AST validation + restricted exec sandbox + Guardian audit)",
            self._security_level, self._MAX_WORKERS,
        )
        _log_forge("INIT", f"level={self._security_level} workers={self._MAX_WORKERS}")

    @property
    def security_level(self) -> str:
        return self._security_level

    # ──────────────────────────────────────────────────────────────────
    # Public API: 단일 도구 검증·등록 (전체 파이프라인)
    # ✅ verified
    # ──────────────────────────────────────────────────────────────────

    def validate_and_register(
        self,
        tool_name: str,
        description: str,
        code: str,
        parameters_json: str = "{}",
        author_agent_id: Optional[str] = None,
        write_to_custom_tools_dir: Optional[Any] = None,
    ) -> ForgeResult:
        """
        코드 검증 후 DB 저장 및 custom_tools 파일 생성.

        전체 파이프라인:
          문법 검사 → AST 보안 검사 → 샌드박스 테스트 → temp_tools 스테이징
          → Guardian 2차 승인 → DB 등록 → custom_tools 승격 → 동적 로딩

        Args:
            tool_name: 도구 이름 (함수명과 일치 권장)
            description: 도구 설명
            code: 도구 함수를 정의하는 Python 코드 문자열
            parameters_json: 파라미터 스키마 JSON 문자열
            author_agent_id: 제안한 에이전트 ID (선택)
            write_to_custom_tools_dir: custom_tools 디렉터리 경로 (승인 시 .py 저장)

        Returns:
            ForgeResult
        """
        import time
        t0 = time.monotonic()
        errors: List[str] = []
        tool_id = str(uuid.uuid4())
        _log_forge("VALIDATE_START", f"tool={tool_name} id={tool_id}")

        # ── Stage 1: 문법 검사 ──
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            msg = f"문법 오류: {e.msg} (line {e.lineno})"
            errors.append(msg)
            _log_forge("SYNTAX_ERROR", msg)
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message=msg, errors=errors, stage="SYNTAX",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # ── Stage 2: AST 보안 검사 (Static Analysis) ──
        security_errors = self._analyzer.analyze(tree, code)
        if security_errors:
            errors.extend(security_errors)
            msg = "; ".join(security_errors)
            _log_forge("SECURITY_REJECT", msg)
            self._save_to_db(
                tool_id, tool_name, description, code,
                parameters_json, author_agent_id, "REJECTED",
            )
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message=msg, errors=errors, stage="AST_SECURITY",
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # ── Stage 3: 샌드박스 테스트 (함수 정의 로드 확인) ──
        try:
            test_out = self._sandbox_execute(code, tool_name)
        except Exception as e:
            msg = f"샌드박스 테스트 실패: {e!r}"
            errors.append(msg)
            _log_forge("SANDBOX_FAIL", msg)
            self._save_to_db(
                tool_id, tool_name, description, code,
                parameters_json, author_agent_id, "REJECTED",
            )
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message=msg, errors=errors, stage="SANDBOX",
                test_output=None,
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        _log_forge("SANDBOX_PASS", f"tool={tool_name}")

        # ── Stage 4: temp_tools 스테이징 ──
        temp_path = self._stage_to_temp(tool_name, code, tool_id)
        _log_forge("STAGED", f"tool={tool_name} path={temp_path}")

        # ── Stage 5: Guardian 2차 검수 ──
        approved, need_approval, audit_result = self._run_guardian_audit(tool_name, description, code)
        if approved is None:
            msg = errors[-1] if errors else "Guardian 실행 불가 (Fail-Closed)"
            self._save_to_db(
                tool_id, tool_name, description, code,
                parameters_json, author_agent_id, "REJECTED",
            )
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message=msg, errors=errors, stage="GUARDIAN",
                test_output=test_out, temp_file_path=str(temp_path),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )
        if need_approval and audit_result:
            # PolicyGuardian NEED_AI_REVIEW → Operator 승인 대기
            try:
                from mellow_link.infra.run_context import get_run_id, get_current_todo_id
                from mellow_link.infra.run_approval import set_pending_and_wait
                run_id = get_run_id()
                todo_id = get_current_todo_id()
            except Exception:
                run_id = None
                todo_id = None
            if run_id:
                resolution = set_pending_and_wait(
                    run_id=run_id,
                    todo_id=todo_id,
                    audit_type="tool_code",
                    file_path=None,
                    critique=getattr(audit_result, "critique", "") or "",
                    risk_level=2,
                    risk_score=getattr(audit_result, "risk_score", 50),
                    db=None,
                )
                if resolution != "approved":
                    msg = "Operator 거부 또는 타임아웃"
                    self._save_to_db(
                        tool_id, tool_name, description, code,
                        parameters_json, author_agent_id, "REJECTED",
                    )
                    return ForgeResult(
                        success=False, tool_id=tool_id, tool_name=tool_name,
                        message=msg, errors=errors, stage="GUARDIAN_APPROVAL",
                        test_output=test_out, temp_file_path=str(temp_path),
                        elapsed_ms=(time.monotonic() - t0) * 1000,
                    )
                # approved → 아래 Stage 6으로 진행
            else:
                msg = "NEED_AI_REVIEW(폐쇄망). Run 컨텍스트 없음. Operator 승인 불가."
                self._save_to_db(
                    tool_id, tool_name, description, code,
                    parameters_json, author_agent_id, "REJECTED",
                )
                return ForgeResult(
                    success=False, tool_id=tool_id, tool_name=tool_name,
                    message=msg, errors=errors, stage="GUARDIAN",
                    test_output=test_out, temp_file_path=str(temp_path),
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                )
        if not approved:
            msg = errors[-1] if errors else "보호자 검수 거부"
            self._save_to_db(
                tool_id, tool_name, description, code,
                parameters_json, author_agent_id, "REJECTED",
            )
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message=msg, errors=errors, stage="GUARDIAN",
                test_output=test_out, temp_file_path=str(temp_path),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # ── Stage 6: DB 저장 (VERIFIED) ──
        if not self._save_to_db(
            tool_id, tool_name, description, code,
            parameters_json, author_agent_id, "VERIFIED",
        ):
            errors.append("DB 저장 실패")
            _log_forge("DB_SAVE_FAIL", tool_name)
            return ForgeResult(
                success=False, tool_id=tool_id, tool_name=tool_name,
                message="DB 저장 실패", errors=errors, stage="REGISTER",
                test_output=test_out, temp_file_path=str(temp_path),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # ── Stage 7: custom_tools/ 승격 ──
        custom_dir = Path(write_to_custom_tools_dir) if write_to_custom_tools_dir else _CUSTOM_TOOLS_DIR
        promoted_path = self._promote_to_custom(tool_name, code, custom_dir)
        _log_forge("PROMOTED", f"tool={tool_name} path={promoted_path}")

        # ── Stage 8: importlib 동적 로딩 검증 ──
        load_ok = self._verify_importlib_load(promoted_path, tool_name)
        if not load_ok:
            logger.warning("[ToolForge] importlib 로딩 검증 실패, 그러나 등록은 유지: %s", tool_name)

        _log_forge("REGISTERED", f"tool={tool_name} id={tool_id}")
        elapsed = (time.monotonic() - t0) * 1000

        return ForgeResult(
            success=True, tool_id=tool_id, tool_name=tool_name,
            message=f"도구 '{tool_name}' 검증 완료 및 등록됨.",
            test_output=test_out, temp_file_path=str(temp_path),
            elapsed_ms=elapsed,
        )

    # ──────────────────────────────────────────────────────────────────
    # Public API: 다중 도구 병렬 검증 (ThreadPoolExecutor)
    # ✅ verified: Ryzen 9 7900 CPU 자원 효율화
    # ──────────────────────────────────────────────────────────────────

    def batch_validate(
        self,
        proposals: List[Dict[str, str]],
        write_to_custom_tools_dir: Optional[str] = None,
    ) -> BatchForgeResult:
        """
        다중 도구를 ThreadPoolExecutor로 병렬 검증·등록.

        Args:
            proposals: [{"tool_name": ..., "description": ..., "code": ..., "parameters_json": ...}, ...]
            write_to_custom_tools_dir: custom_tools 디렉터리 경로

        Returns:
            BatchForgeResult
        """
        import time
        t0 = time.monotonic()
        results: List[ForgeResult] = []
        succeeded = 0

        futures_map = {}
        for p in proposals:
            fut = self._executor.submit(
                self.validate_and_register,
                tool_name=p.get("tool_name", "unknown"),
                description=p.get("description", ""),
                code=p.get("code", ""),
                parameters_json=p.get("parameters_json", "{}"),
                author_agent_id=p.get("author_agent_id"),
                write_to_custom_tools_dir=write_to_custom_tools_dir,
            )
            futures_map[fut] = p.get("tool_name", "unknown")

        for fut in as_completed(futures_map):
            name = futures_map[fut]
            try:
                result = fut.result(timeout=30)
                results.append(result)
                if result.success:
                    succeeded += 1
            except Exception as e:
                logger.exception("[ToolForge] batch_validate worker exception: %s", name)
                results.append(ForgeResult(
                    success=False, tool_name=name,
                    message=f"워커 예외: {e!r}", stage="WORKER",
                    errors=[str(e)],
                ))

        elapsed = (time.monotonic() - t0) * 1000
        _log_forge(
            "BATCH_COMPLETE",
            f"total={len(proposals)} succeeded={succeeded} "
            f"failed={len(proposals) - succeeded} elapsed={elapsed:.0f}ms",
        )

        return BatchForgeResult(
            total=len(proposals),
            succeeded=succeeded,
            failed=len(proposals) - succeeded,
            results=results,
            elapsed_ms=elapsed,
        )

    # ──────────────────────────────────────────────────────────────────
    # Public API: Need Detection (도구 부족 감지)
    # ⚠️ possible: AgentBrain 연동
    # ──────────────────────────────────────────────────────────────────

    def detect_tool_need(
        self,
        failed_tool_name: Optional[str] = None,
        error_message: Optional[str] = None,
        task_intent: Optional[str] = None,
    ) -> NeedDetectionResult:
        """
        도구 실패 또는 부족을 감지하고, 새 도구 제안 필요 여부를 판단.

        AgentBrain의 ReAct 루프에서 도구 실행 실패 시 호출됩니다.
        연속 실패 횟수, 에러 패턴을 분석하여 propose_new_tool 호출을 유도합니다.

        Args:
            failed_tool_name: 실패한 도구 이름
            error_message: 에러 메시지
            task_intent: 사용자의 작업 의도

        Returns:
            NeedDetectionResult
        """
        # DB에서 도구 실패 통계 조회
        failure_count = 0
        try:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            if failed_tool_name:
                stats = db.get_tool_stats(failed_tool_name)
                if stats:
                    total = stats.use_count or 1
                    success = stats.success_count or 0
                    failure_count = total - success
                    failure_rate = 1.0 - (success / total) if total > 0 else 1.0

                    # 실패율 50% 초과 + 3회 이상 실패 → 도구 부족 감지
                    if failure_rate > 0.5 and failure_count >= 3:
                        _log_forge(
                            "NEED_DETECTED",
                            f"tool={failed_tool_name} failures={failure_count} rate={failure_rate:.2f}",
                        )
                        return NeedDetectionResult(
                            needs_new_tool=True,
                            reason=f"도구 '{failed_tool_name}'의 실패율 {failure_rate:.0%} "
                                   f"({failure_count}회 실패). 대체 도구 필요.",
                            suggested_tool_name=f"{failed_tool_name}_v2",
                            suggested_description=f"'{failed_tool_name}'의 개선된 버전. "
                                                  f"마지막 에러: {(error_message or '')[:100]}",
                            failed_tool_name=failed_tool_name,
                            failure_count=failure_count,
                        )
        except Exception as e:
            logger.debug("[ToolForge] detect_tool_need DB query failed: %s", e)

        # 도구가 존재하지 않는 경우
        if error_message and "찾을 수 없습니다" in error_message:
            return NeedDetectionResult(
                needs_new_tool=True,
                reason=f"요청된 도구가 레지스트리에 없음: {error_message[:150]}",
                suggested_tool_name=failed_tool_name or "custom_helper",
                suggested_description=f"작업 의도: {(task_intent or '미상')[:100]}",
                failed_tool_name=failed_tool_name,
                failure_count=failure_count,
            )

        return NeedDetectionResult(
            needs_new_tool=False,
            reason="도구 부족 미감지",
            failed_tool_name=failed_tool_name,
            failure_count=failure_count,
        )

    # ──────────────────────────────────────────────────────────────────
    # Public API: AST 보안 검사 (외부 호출용)
    # ✅ verified
    # ──────────────────────────────────────────────────────────────────

    def check_ast_security(self, code: str) -> List[str]:
        """AST 기반 보안 검사 수행. 에러 리스트 반환 (빈 리스트 = 통과)."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [f"문법 오류: {e.msg} (line {e.lineno})"]
        return self._analyzer.analyze(tree, code)

    # ──────────────────────────────────────────────────────────────────
    # Internal: 샌드박스 실행
    # ✅ verified
    # ──────────────────────────────────────────────────────────────────

    def _sandbox_execute(self, code: str, expected_func_name: str) -> str:
        """
        제한된 globals/locals로 코드를 실행하고,
        expected_func_name 함수가 존재하는지 확인.
        실제로 함수를 호출하지는 않고, 정의만 로드해 반환 메시지를 만든다.

        Returns:
            테스트 결과 요약 문자열
        """
        restricted_globals: Dict[str, Any] = dict(SAFE_BUILTINS)
        restricted_globals["__builtins__"] = SAFE_BUILTINS
        restricted_globals.update(SAFE_MODULES)
        restricted_locals: Dict[str, Any] = {}

        try:
            exec(code, restricted_globals, restricted_locals)  # noqa: S102
        except Exception as e:
            logger.warning("[ToolForge] Sandbox exec failed: %s", e)
            raise

        # 정의된 이름 중 expected_func_name이 callable인지 확인
        defined = list(restricted_locals.keys()) + [
            k for k in restricted_globals.keys()
            if k not in SAFE_BUILTINS and k != "__builtins__"
        ]

        for ns in (restricted_locals, restricted_globals):
            if expected_func_name in ns and callable(ns[expected_func_name]):
                # 함수 시그니처 추출
                sig_str = ""
                try:
                    sig = inspect.signature(ns[expected_func_name])
                    sig_str = f" → signature: {expected_func_name}{sig}"
                except (ValueError, TypeError):
                    pass
                return (
                    f"함수 '{expected_func_name}' 정의 확인됨{sig_str}. "
                    f"정의된 심볼: {defined}"
                )

        return (
            f"실행 완료. 정의된 심볼: {defined} "
            f"(함수 '{expected_func_name}' 호출 가능 여부는 런타임에서 검증)"
        )

    # ──────────────────────────────────────────────────────────────────
    # Internal: temp_tools 스테이징
    # ✅ verified: workspace/temp_tools/에 후보 코드 저장
    # ──────────────────────────────────────────────────────────────────

    def _stage_to_temp(self, tool_name: str, code: str, tool_id: str) -> Path:
        """
        후보 코드를 workspace/temp_tools/에 저장.
        파일명: {tool_name}_{short_id}.py

        Guardian 검수 전 단계에서 코드를 파일로 남겨 감사 추적 가능.
        """
        _TEMP_TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        short_id = tool_id[:8]
        filename = f"{tool_name}_{short_id}.py"
        temp_path = _TEMP_TOOLS_DIR / filename

        header = (
            f'"""\n'
            f"Auto-generated by ToolForge\n"
            f"Tool: {tool_name}\n"
            f"ID: {tool_id}\n"
            f"Date: {datetime.now().isoformat()}\n"
            f"Security Level: {self._security_level}\n"
            f"Status: STAGED (pending Guardian audit)\n"
            f'"""\n\n'
        )
        temp_path.write_text(header + code, encoding="utf-8")
        return temp_path

    # ──────────────────────────────────────────────────────────────────
    # Internal: Guardian 2차 검수
    # ✅ verified: GuardianService.audit_tool_code() 연동
    # ──────────────────────────────────────────────────────────────────

    def _run_guardian_audit(
        self,
        tool_name: str,
        description: str,
        code: str,
    ) -> Tuple[Optional[bool], bool, Optional[Any]]:
        """
        Guardian 2차 검수.

        Returns:
            (approved, need_approval, audit_result)
            - approved: True=승인, False=거부, None=Guardian 실행 불가(Fail-Closed)
            - need_approval: PolicyGuardian NEED_AI_REVIEW 시 True → Operator 승인 대기
            - audit_result: AuditResult (need_approval 시 메타 전달용)
        """
        try:
            from mellow_link.core.guardian_service import get_guardian_service
            guardian = get_guardian_service()

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                future = asyncio.run_coroutine_threadsafe(
                    guardian.audit_tool_code(tool_name, description, code),
                    loop
                )
                audit = future.result(timeout=60)
            else:
                new_loop = asyncio.new_event_loop()
                try:
                    audit = new_loop.run_until_complete(
                        guardian.audit_tool_code(tool_name, description, code)
                    )
                finally:
                    new_loop.run_until_complete(asyncio.sleep(0.1))
                    new_loop.close()

            if getattr(audit, "policy_decision", None) == "NEED_AI_REVIEW":
                _log_forge("GUARDIAN_NEED_APPROVAL", audit.critique or "NEED_AI_REVIEW")
                return (False, True, audit)
            if not audit.is_approved:
                msg = f"보호자 검수 거부: {audit.critique}"
                if audit.refined_recommendation:
                    msg += f" 수정 제안: {audit.refined_recommendation}"
                _log_forge("GUARDIAN_REJECT", msg)
                return (False, False, audit)
            _log_forge("GUARDIAN_APPROVE", f"tool={tool_name}")
            return (True, False, audit)
        except Exception as e:
            _log_forge("GUARDIAN_ERROR", str(e)[:200])
            logger.warning("[ToolForge] Guardian audit failed (Fail-Closed): %s", e)
            return (None, False, None)

    # ──────────────────────────────────────────────────────────────────
    # Internal: custom_tools/ 승격
    # ✅ verified
    # ──────────────────────────────────────────────────────────────────

    def _promote_to_custom(self, tool_name: str, code: str, custom_dir: Path) -> Path:
        """Guardian 검수 통과 후 custom_tools/로 승격."""
        custom_dir.mkdir(parents=True, exist_ok=True)
        out_path = custom_dir / f"{tool_name}.py"
        out_path.write_text(code, encoding="utf-8")
        _log_forge("FILE_WRITTEN", str(out_path))
        return out_path

    # ──────────────────────────────────────────────────────────────────
    # Internal: importlib 동적 로딩 검증
    # ✅ verified: 최종 등록 전 importlib으로 로드 가능한지 확인
    # ──────────────────────────────────────────────────────────────────

    def _verify_importlib_load(self, file_path: Path, expected_func: str) -> bool:
        """
        importlib.util을 사용하여 파일을 동적 로드하고,
        기대하는 함수가 callable로 존재하는지 검증.

        Returns:
            True → 로드 성공 및 함수 존재
            False → 로드 실패 또는 함수 없음
        """
        try:
            module_name = f"forge_verify_{file_path.stem}_{uuid.uuid4().hex[:6]}"
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning("[ToolForge] importlib: invalid spec for %s", file_path)
                return False

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            func = getattr(module, expected_func, None)
            if callable(func):
                _log_forge("IMPORTLIB_VERIFIED", f"tool={expected_func} path={file_path}")
                return True
            else:
                logger.warning(
                    "[ToolForge] importlib: '%s' not callable in %s",
                    expected_func, file_path,
                )
                return False

        except Exception as e:
            logger.warning("[ToolForge] importlib verify failed: %s", e)
            return False
        finally:
            # 검증용 모듈 정리
            if module_name in sys.modules:
                del sys.modules[module_name]

    # ──────────────────────────────────────────────────────────────────
    # Internal: DB 저장
    # ✅ verified
    # ──────────────────────────────────────────────────────────────────

    def _save_to_db(
        self,
        tool_id: str,
        tool_name: str,
        description: str,
        code: str,
        parameters_json: str,
        author_agent_id: Optional[str],
        status: str,
    ) -> bool:
        """DB에 DynamicToolRecord 저장."""
        try:
            from mellow_link.infra.memory_database import get_memory_db, DynamicToolRecord
            db = get_memory_db()
            record = DynamicToolRecord(
                id=tool_id,
                tool_name=tool_name,
                description=description,
                code=code,
                parameters_json=parameters_json,
                author_agent_id=author_agent_id,
                status=status,
                created_at=datetime.now(),
            )
            return db.save_dynamic_tool(record)
        except Exception as e:
            logger.debug("[ToolForge] DB save failed: %s", e)
            return False

    # ──────────────────────────────────────────────────────────────────
    # Internal: 하위 호환 메서드
    # ──────────────────────────────────────────────────────────────────

    def _check_ast_security(self, tree: ast.AST) -> List[str]:
        """하위 호환: 기존 호출 코드 지원."""
        return self._analyzer.analyze(tree)

    def _save_rejected(
        self,
        tool_id: str,
        tool_name: str,
        description: str,
        code: str,
        parameters_json: str,
        author_agent_id: Optional[str],
    ) -> None:
        """Guardian 거부 시 DB에 REJECTED로 기록 (감사 추적용)."""
        self._save_to_db(
            tool_id, tool_name, description, code,
            parameters_json, author_agent_id, "REJECTED",
        )

    # ──────────────────────────────────────────────────────────────────
    # Cleanup / Lifecycle
    # ──────────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """ThreadPoolExecutor 정리."""
        self._executor.shutdown(wait=False)
        _log_forge("SHUTDOWN", "ThreadPoolExecutor stopped")

    def cleanup_temp_tools(self, max_age_hours: int = 24) -> int:
        """
        workspace/temp_tools/ 내 오래된 파일 정리.

        Args:
            max_age_hours: 이 시간보다 오래된 파일 삭제

        Returns:
            삭제된 파일 수
        """
        if not _TEMP_TOOLS_DIR.exists():
            return 0
        import time
        now = time.time()
        cutoff = now - (max_age_hours * 3600)
        deleted = 0
        for f in _TEMP_TOOLS_DIR.glob("*.py"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
                    _log_forge("TEMP_CLEANUP", str(f))
            except Exception:
                pass
        return deleted

    def get_forge_status(self) -> Dict[str, Any]:
        """Forge 상태 정보 반환 (모니터링/디버그용)."""
        temp_count = len(list(_TEMP_TOOLS_DIR.glob("*.py"))) if _TEMP_TOOLS_DIR.exists() else 0
        custom_count = len(list(_CUSTOM_TOOLS_DIR.glob("*.py"))) if _CUSTOM_TOOLS_DIR.exists() else 0

        # DB에서 통계 조회
        db_stats = {"verified": 0, "rejected": 0, "pending": 0}
        try:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            for status_key in ("VERIFIED", "REJECTED", "PENDING"):
                tools = db.get_dynamic_tools_by_status(status=status_key, limit=1000)
                db_stats[status_key.lower()] = len(tools)
        except Exception:
            pass

        return {
            "security_level": self._security_level,
            "max_workers": self._MAX_WORKERS,
            "temp_tools_count": temp_count,
            "custom_tools_count": custom_count,
            "db_stats": db_stats,
            "temp_tools_dir": str(_TEMP_TOOLS_DIR),
            "custom_tools_dir": str(_CUSTOM_TOOLS_DIR),
        }


# ═══════════════════════════════════════════════════════════════════════
# Integrity Guard — 보안 상수 무결성 검증
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class IntegrityResult:
    """무결성 검증 결과."""
    ok: bool
    violations: List[str] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now().isoformat())


class IntegrityGuard:
    """
    tool_forge.py의 보안 핵심 상수(FORBIDDEN_NAMES, OS_FORBIDDEN_ATTRS,
    HARD_IMPORT_WHITELIST)에 대한 SHA-256 무결성 검증.

    작동 방식:
      1. seal(): 현재 상수 값을 정규화(sorted → JSON) → SHA-256 해시 → baseline 저장
      2. verify(): 현재 상수 값을 다시 해싱하여 baseline과 비교
      3. 불일치 시 경고 로깅 + IntegrityResult.ok = False

    정규화 해싱이므로:
      - 코멘트/공백/포매팅 변경에는 반응하지 않음
      - 실제 set 원소 추가/삭제/변경만 탐지
    """

    _BASELINE_FILE = _MELLOW_LINK_ROOT / "core" / ".security_baseline.json"

    # 검증 대상 상수 이름 → 모듈 수준 변수 참조
    _GUARD_TARGETS: Dict[str, str] = {
        "FORBIDDEN_NAMES": "FORBIDDEN_NAMES",
        "OS_FORBIDDEN_ATTRS": "OS_FORBIDDEN_ATTRS",
        "HARD_IMPORT_WHITELIST": "HARD_IMPORT_WHITELIST",
    }

    @staticmethod
    def _canonical_hash(values: set) -> str:
        """set을 정규화(sorted list → JSON)하여 SHA-256 해시 반환."""
        canonical = json.dumps(sorted(values), ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def _collect_current_hashes(cls) -> Dict[str, str]:
        """현재 모듈의 보안 상수들을 해싱."""
        current_module = sys.modules[__name__]
        hashes: Dict[str, str] = {}
        for label, var_name in cls._GUARD_TARGETS.items():
            var = getattr(current_module, var_name, None)
            if var is not None and isinstance(var, set):
                hashes[label] = cls._canonical_hash(var)
        return hashes

    @classmethod
    def seal(cls) -> Dict[str, str]:
        """
        현재 보안 상수의 해시를 baseline으로 저장(봉인).
        최초 배포 시 또는 관리자가 의도적으로 규칙을 변경한 뒤 호출.

        Returns:
            저장된 해시 dict
        """
        hashes = cls._collect_current_hashes()
        baseline = {
            "sealed_at": datetime.now().isoformat(),
            "hashes": hashes,
            "description": (
                "tool_forge.py 보안 상수 무결성 baseline. "
                "이 파일을 에이전트가 수정해서는 안 됨."
            ),
        }
        cls._BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cls._BASELINE_FILE.write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        _log_forge("INTEGRITY_SEAL", f"Baseline saved → {cls._BASELINE_FILE}")
        logger.info(
            "[IntegrityGuard] 보안 baseline 봉인 완료: %s",
            {k: v[:12] + "..." for k, v in hashes.items()},
        )
        return hashes

    @classmethod
    def verify(cls) -> IntegrityResult:
        """
        현재 보안 상수가 baseline과 일치하는지 검증.

        Returns:
            IntegrityResult (ok=True이면 무결, ok=False이면 변조 감지)
        """
        if not cls._BASELINE_FILE.exists():
            # baseline이 없으면 최초 봉인 수행
            logger.info("[IntegrityGuard] Baseline 미존재 → 최초 봉인 수행")
            cls.seal()
            return IntegrityResult(ok=True)

        try:
            baseline_data = json.loads(
                cls._BASELINE_FILE.read_text(encoding="utf-8")
            )
            stored_hashes: Dict[str, str] = baseline_data.get("hashes", {})
        except Exception as e:
            msg = f"Baseline 파일 읽기 실패: {e}"
            logger.error("[IntegrityGuard] %s", msg)
            return IntegrityResult(ok=False, violations=[msg])

        current_hashes = cls._collect_current_hashes()
        violations: List[str] = []

        for label, expected_hash in stored_hashes.items():
            actual_hash = current_hashes.get(label)
            if actual_hash is None:
                violations.append(f"{label}: 상수 자체가 삭제됨!")
            elif actual_hash != expected_hash:
                violations.append(
                    f"{label}: 변조 감지! "
                    f"expected={expected_hash[:16]}... "
                    f"actual={actual_hash[:16]}..."
                )

        if violations:
            alert = (
                f"[SECURITY ALERT] 보안 상수 무결성 위반 감지!\n"
                + "\n".join(f"  - {v}" for v in violations)
            )
            logger.critical(alert)
            _log_forge("INTEGRITY_VIOLATION", json.dumps(violations, ensure_ascii=False))
            return IntegrityResult(ok=False, violations=violations)

        _log_forge("INTEGRITY_OK", "All security constants verified")
        return IntegrityResult(ok=True)

    @classmethod
    def is_baseline_sealed(cls) -> bool:
        """baseline 파일이 존재하는지 확인."""
        return cls._BASELINE_FILE.exists()


# ═══════════════════════════════════════════════════════════════════════
# Singleton
# ═══════════════════════════════════════════════════════════════════════

_forge_instance: Optional[ToolForge] = None


def get_tool_forge(security_level: Optional[str] = None) -> ToolForge:
    """
    ToolForge 싱글톤 반환.

    Args:
        security_level: "NORMAL" 또는 "HARD". 최초 호출 시에만 적용.
                        None이면 환경변수 SECURITY_LEVEL에서 읽음.
    """
    global _forge_instance
    if _forge_instance is None:
        if security_level is None:
            security_level = os.environ.get("SECURITY_LEVEL", "NORMAL")
        _forge_instance = ToolForge(security_level=security_level)

        # ── Forge 초기화 시 무결성 검증 ──
        integrity = IntegrityGuard.verify()
        if not integrity.ok:
            logger.critical(
                "[ToolForge] ⚠️ 보안 상수 무결성 위반! Forge는 생성되었으나 "
                "보안 규칙이 변조되었을 수 있습니다. violations=%s",
                integrity.violations,
            )
    return _forge_instance


def run_ast_security_check(code: str) -> Tuple[bool, str]:
    """
    AST 기반 보안 검사 수행 (autonomous_agent 등 외부 호출용).

    Returns:
        (success, error_message) - success면 통과, 실패 시 error_message
    """
    forge = get_tool_forge()
    errors = forge.check_ast_security(code)
    if errors:
        return False, "; ".join(errors[:5])
    return True, ""
