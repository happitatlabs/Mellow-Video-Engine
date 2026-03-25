"""
SecurityManager: "Difficulty Tier" 기반 보안 정책 스위치.

요구사항:
  - SECURITY_LEVEL (EASY / NORMAL / HARD) 값을 환경변수(.env 포함) 또는 설정에서 읽는다.
  - EASY: 개발 편의성 중심. 모든 파일 쓰기 허용, 실행 확인/제한 최소화.
  - NORMAL: (기본값) workspace(sandbox) 격리 + 시스템/코어 파일 보호 + 코드 수정은 PR/제안으로만.
  - HARD: 무인 운영용. 샌드박스/화이트리스트/민감정보 보호 강화.

이 클래스의 목표는 "정책 결정"을 한 곳에 모아, 에이전트 행동 반경을 설정값 하나로 즉시 바꾸는 것이다.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import logging

from mellow_link.core.path_manager import PathManager

logger = logging.getLogger(__name__)


SecurityLevel = str  # "EASY" | "NORMAL" | "HARD"


class SecurityBlocked(PermissionError):
    """보안 정책 위반으로 동작이 차단되었음을 의미."""

    def __init__(self, message: str, *, proposal_path: Optional[Path] = None):
        super().__init__(message)
        self.proposal_path = proposal_path


def _normalize_level(raw: object) -> SecurityLevel:
    v = (raw or "").strip().upper() if isinstance(raw, str) else ""
    if v in {"EASY", "NORMAL", "HARD"}:
        return v
    return "NORMAL"


def _truthy_env(name: str) -> bool:
    v = os.getenv(name)
    if not isinstance(v, str):
        return False
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _default_sandbox_root() -> Path:
    """
    기본 sandbox_root를 계산한다.
    - 이 파일은 <repo>/core/security_manager.py 에 있으므로, repo 루트는 parents[1].
    """
    return Path(__file__).resolve().parents[1]


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class CommandPolicy:
    """
    명령 실행 정책(화이트리스트/메타문자 차단).
    """

    allow_any_command: bool
    allowed_commands: frozenset[str]
    block_shell_metachar: bool


@dataclass(frozen=True)
class FilePolicy:
    """
    파일 접근 정책(샌드박스/쓰기 허용 디렉토리/민감 파일 보호).
    """

    sandbox_root: Path
    allow_write_anywhere: bool
    allowed_write_roots: Tuple[Path, ...]  # sandbox_root 기준 하위만 의미있음
    protected_write_roots: Tuple[Path, ...]
    protected_write_patterns: Tuple[re.Pattern[str], ...]
    protected_read_patterns: Tuple[re.Pattern[str], ...]
    hard_allowed_extensions: Tuple[str, ...]


class SecurityManager:
    """
    난이도 티어(EASY/NORMAL/HARD)에 따라 정책을 결정하고, 파일/명령 실행을 게이트한다.
    """

    # NORMAL 기본 allowlist(기존 테스트/동작 유지)
    _NORMAL_ALLOWED_COMMANDS = frozenset({"curl", "ping", "nslookup", "ipconfig", "whoami"})
    _HARD_ALLOWED_COMMANDS = frozenset({"whoami", "ipconfig"})

    # 셸 메타 문자(체인/파이프/리다이렉트/서브셸) 차단 패턴
    _SHELL_METACHAR = re.compile(r"[;&|`$><\n]|&&|\|\|")

    # HARD에서 외부 HTTP를 허용하려면 명시적으로 켜야 함
    _HARD_OUTBOUND_OVERRIDE_ENV = "MELLOW_ALLOW_OUTBOUND"

    def __init__(self, *, level: SecurityLevel, sandbox_root: Path):
        self._level: SecurityLevel = _normalize_level(level)
        self._sandbox_root: Path = Path(sandbox_root).resolve()
        self._pm = PathManager(str(self._sandbox_root))
        self._file_policy = self._build_file_policy()
        self._cmd_policy = self._build_command_policy()

    # ──────────────────────────────────────────
    # Construction
    # ──────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "SecurityManager":
        """
        환경변수에서 SECURITY_LEVEL을 읽어 인스턴스 생성.

        지원:
          - SECURITY_LEVEL
          - MELLOW_SECURITY_LEVEL
          - MELLOW_LINK_PROJECT_ROOT / PROJECT_ROOT (sandbox_root 추론 보조)
        """
        level = _normalize_level(os.getenv("SECURITY_LEVEL") or os.getenv("MELLOW_SECURITY_LEVEL") or "NORMAL")

        # sandbox_root:
        # - 기본은 "mellow_link 레포/패키지 루트"를 사용한다.
        # - 런처가 PROJECT_ROOT(예: D:\AI_Project)만 주는 경우가 많아서,
        #   그 아래에 mellow_link/가 있으면 그 하위를 sandbox로 고정한다.
        root_hint = os.environ.get("MELLOW_LINK_PROJECT_ROOT") or os.environ.get("PROJECT_ROOT") or ""
        base = Path(root_hint).resolve() if root_hint else _default_sandbox_root()

        # 1) base 자체가 mellow_link 루트인지 검사
        if (base / "core").exists() and (base / "config").exists():
            sandbox_root = base
        # 2) base 아래에 mellow_link/가 있으면 그쪽을 sandbox로
        elif (base / "mellow_link" / "core").exists() and (base / "mellow_link" / "config").exists():
            sandbox_root = (base / "mellow_link")
        else:
            sandbox_root = _default_sandbox_root()

        return cls(level=level, sandbox_root=sandbox_root)

    @classmethod
    def from_settings_or_env(cls) -> "SecurityManager":
        """
        가능한 경우 settings(SecurityLevel)을 우선 사용, 실패 시 env로 폴백.
        """
        try:
            from mellow_link.config.settings import get_settings

            s = get_settings()
            level = getattr(s, "security_level", None)
            if isinstance(level, str) and level.strip():
                return cls(level=_normalize_level(level), sandbox_root=_default_sandbox_root())
        except Exception:
            pass
        return cls.from_env()

    # ──────────────────────────────────────────
    # Public properties
    # ──────────────────────────────────────────

    @property
    def level(self) -> SecurityLevel:
        return self._level

    @property
    def sandbox_root(self) -> Path:
        return self._sandbox_root

    @property
    def path_manager(self) -> PathManager:
        """기존 sanitize/safe_join 등을 활용하기 위한 PathManager."""
        return self._pm

    def policy_snapshot(self) -> dict:
        """
        현재 보안 정책을 사람이 읽기 쉬운 형태로 요약.
        (디버깅/운영 점검용)
        """
        fp = self._file_policy
        cp = self._cmd_policy

        def _rel(p: Path) -> str:
            try:
                return str(p.resolve().relative_to(self._sandbox_root))
            except Exception:
                return str(p.resolve())

        return {
            "security_level": self._level,
            "sandbox_root": str(self._sandbox_root),
            "files": {
                "allow_write_anywhere": fp.allow_write_anywhere,
                "allowed_write_roots": [_rel(p) for p in fp.allowed_write_roots],
                "protected_write_roots": [_rel(p) for p in fp.protected_write_roots],
                "protected_write_patterns": [p.pattern for p in fp.protected_write_patterns],
                "hard_allowed_extensions": list(fp.hard_allowed_extensions),
            },
            "commands": {
                "allow_any_command": cp.allow_any_command,
                "allowed_commands": sorted(cp.allowed_commands),
                "block_shell_metachar": cp.block_shell_metachar,
            },
            "outbound_http": {
                "hard_default_deny": True,
                "hard_override_env": self._HARD_OUTBOUND_OVERRIDE_ENV,
                "hard_override_enabled": _truthy_env(self._HARD_OUTBOUND_OVERRIDE_ENV),
            },
        }

    # ──────────────────────────────────────────
    # Policy building
    # ──────────────────────────────────────────

    def _build_command_policy(self) -> CommandPolicy:
        if self._level == "EASY":
            return CommandPolicy(
                allow_any_command=True,
                allowed_commands=frozenset(),
                block_shell_metachar=False,
            )
        if self._level == "HARD":
            return CommandPolicy(
                allow_any_command=False,
                allowed_commands=self._HARD_ALLOWED_COMMANDS,
                block_shell_metachar=True,
            )
        # NORMAL (default)
        return CommandPolicy(
            allow_any_command=False,
            allowed_commands=self._NORMAL_ALLOWED_COMMANDS,
            block_shell_metachar=True,
        )

    def _build_file_policy(self) -> FilePolicy:
        sandbox = self._sandbox_root

        # NORMAL/HARD에서 "코드 직접 수정"을 막기 위한 보호 루트(대부분 코어/서비스/설정)
        protected_write_roots = (
            # --- root-level critical assets (explicit protection) ---
            sandbox / "main.py",
            sandbox / "__init__.py",
            sandbox / ".env",
            sandbox / "prompts",
            # --- package directories ---
            sandbox / "core",
            sandbox / "services",
            sandbox / "infra",
            sandbox / "utils",
            sandbox / "config",
            sandbox / "static",
            sandbox / "extensions",
            sandbox / ".git",
        )

        # HARD에서 민감 파일 읽기 차단(토큰/키/환경 등)
        protected_read_patterns: Tuple[re.Pattern[str], ...] = (
            re.compile(r"(^|[\\/])\.env$", re.IGNORECASE),
            re.compile(r"(^|[\\/])keys([\\/]|$)", re.IGNORECASE),
            re.compile(r"\.(pem|key|p12|pfx)$", re.IGNORECASE),
            re.compile(r"(^|[\\/])id_rsa", re.IGNORECASE),
            re.compile(r"(^|[\\/])\.git([\\/]|$)", re.IGNORECASE),
        )

        # .env 변종(.env.*) 쓰기 금지 (V-11)
        protected_write_patterns: Tuple[re.Pattern[str], ...] = (
            re.compile(r"(^|[\\/])\.env(\.[^\\/]+)?$", re.IGNORECASE),
        )

        # HARD에서 파일 생성 확장자 제한(운영 산출물 중심)
        hard_allowed_extensions = (
            ".txt",
            ".md",
            ".json",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".csv",
            ".log",
        )

        if self._level == "EASY":
            return FilePolicy(
                sandbox_root=sandbox,
                allow_write_anywhere=True,
                allowed_write_roots=tuple(),
                protected_write_roots=tuple(),
                protected_write_patterns=tuple(),
                protected_read_patterns=tuple(),
                hard_allowed_extensions=hard_allowed_extensions,
            )

        # NORMAL/HARD 공통: sandbox 내부에서만 쓰기/읽기
        allowed_write_roots = (
            sandbox / "outputs",
            sandbox / "data",
            sandbox / "debug",
            sandbox / "failed_attempts",
            sandbox / "blog",
            sandbox / "strat",
            sandbox / "test",
            sandbox / "workspace",
        )
        if self._level == "HARD":
            # HARD는 더 좁게: 운영 산출물 중심(필요 최소)
            allowed_write_roots = (sandbox / "outputs", sandbox / "data")

        return FilePolicy(
            sandbox_root=sandbox,
            allow_write_anywhere=False,
            allowed_write_roots=tuple(Path(p).resolve() for p in allowed_write_roots),
            protected_write_roots=tuple(Path(p).resolve() for p in protected_write_roots),
            protected_write_patterns=protected_write_patterns,
            protected_read_patterns=protected_read_patterns,
            hard_allowed_extensions=hard_allowed_extensions,
        )

    # ──────────────────────────────────────────
    # File guards
    # ──────────────────────────────────────────

    def resolve_for_read(self, target_path: str | Path) -> Path:
        """
        읽기 대상 경로를 정책에 따라 정규화/검증하여 반환.
        """
        if self._level == "EASY":
            p = Path(target_path)
            if not p.is_absolute():
                p = (self._sandbox_root / p)
            return p.resolve()

        # NORMAL/HARD: sandbox 탈출 차단 (PathManager가 traversal/symlink 우회 포함 방어)
        safe = self._pm.validate(target_path)

        if self._level in {"NORMAL", "HARD"}:
            rel = safe.resolve().as_posix()
            rel2 = str(safe.resolve()).replace("\\", "/")
            for pat in self._file_policy.protected_read_patterns:
                if pat.search(rel) or pat.search(rel2):
                    raise SecurityBlocked(f"[차단] {self._level}: 민감 파일 읽기 금지: {safe}")
        return safe

    def resolve_for_write(self, target_path: str | Path, *, content: str = "") -> Path:
        """
        쓰기 대상 경로를 정책에 따라 정규화/검증하여 반환.
        정책 위반 시 SecurityBlocked 예외를 발생시키며, NORMAL/HARD에서는 '제안서'를 생성할 수 있다.
        """
        # EASY: 절대/상대 모두 허용(상대는 sandbox_root 기준)
        if self._file_policy.allow_write_anywhere:
            p = Path(target_path)
            if not p.is_absolute():
                p = (self._sandbox_root / p)
            return p.resolve()

        # NORMAL/HARD: sandbox 내부만
        safe = self._pm.validate(target_path)
        safe_resolved = safe.resolve()

        # 0) 보호 패턴(.env.* 등) 직접 수정 금지
        rel_posix = safe_resolved.as_posix()
        rel_win = str(safe_resolved).replace("\\", "/")
        for pat in self._file_policy.protected_write_patterns:
            if pat.search(rel_posix) or pat.search(rel_win):
                return self._block_write_as_proposal(
                    safe_resolved,
                    content=content,
                    reason=f"보호된 파일 패턴 직접 수정 금지: {self._pm.get_relative(safe_resolved)}",
                )

        # 1) 보호 루트(코어/설정/서비스 등) 직접 수정 금지
        for pr in self._file_policy.protected_write_roots:
            if safe_resolved == pr or _is_relative_to(safe_resolved, pr):
                return self._block_write_as_proposal(
                    safe_resolved,
                    content=content,
                    reason=f"보호된 경로 직접 수정 금지: {self._pm.get_relative(safe_resolved)}",
                )

        # 2) sandbox 내부라도 허용된 쓰기 루트 밖이면 차단 + 제안
        if not any(_is_relative_to(safe_resolved, ar) for ar in self._file_policy.allowed_write_roots):
            return self._block_write_as_proposal(
                safe_resolved,
                content=content,
                reason=f"허용된 쓰기 영역 밖: {self._pm.get_relative(safe_resolved)}",
            )

        # 3) HARD: 확장자 제한
        if self._level == "HARD":
            ext = safe_resolved.suffix.lower()
            if ext and ext not in {e.lower() for e in self._file_policy.hard_allowed_extensions}:
                return self._block_write_as_proposal(
                    safe_resolved,
                    content=content,
                    reason=f"HARD: 허용되지 않은 확장자({ext})",
                )

        return safe

    def _block_write_as_proposal(self, target_path: Path, *, content: str, reason: str) -> Path:
        """
        NORMAL/HARD: 코드/보호영역에 대한 직접 쓰기를 차단하고, 대신 제안서를 outputs/proposals/에 저장한다.
        반환값은 'proposal 파일 경로'가 아닌, 예외를 던지기 위해 사용되며 실제로는 호출부가 예외를 잡아 메시지를 반환한다.
        """
        proposals_dir = (self._sandbox_root / "outputs" / "proposals").resolve()
        proposals_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", target_path.as_posix().strip("/").replace("/", "__"))
        proposal_path = proposals_dir / f"{ts}__proposal__{safe_name}.txt"

        body = (
            "[Mellow-Link Security Proposal]\n"
            f"- security_level: {self._level}\n"
            f"- target_path: {target_path}\n"
            f"- reason: {reason}\n"
            "\n"
            "---- suggested_content_begin ----\n"
            f"{content}\n"
            "---- suggested_content_end ----\n"
        )
        proposal_path.write_text(body, encoding="utf-8")

        raise SecurityBlocked(
            f"[차단] {self._level}: 직접 파일 쓰기 불가. (사유: {reason})",
            proposal_path=proposal_path,
        )

    # ──────────────────────────────────────────
    # Command guards
    # ──────────────────────────────────────────

    def parse_and_validate_command(self, command: str) -> Sequence[str]:
        """
        커맨드 문자열을 토큰화하고 정책(메타문자/화이트리스트)을 적용해 tokens를 반환.
        위반 시 SecurityBlocked 발생.
        """
        if self._cmd_policy.block_shell_metachar and self._SHELL_METACHAR.search(command or ""):
            raise SecurityBlocked("[차단] 셸 메타 문자가 포함되어 있습니다. 단일 명령어만 허용됩니다.")

        try:
            tokens = shlex.split(command)
        except ValueError as e:
            raise SecurityBlocked(f"[차단] 명령어 파싱 실패: {e}")

        if not tokens:
            raise SecurityBlocked("[Error] 빈 명령어입니다.")

        if self._cmd_policy.allow_any_command:
            return tokens

        cmd_name = Path(tokens[0]).stem.lower()  # "curl.exe" -> "curl"
        if cmd_name not in self._cmd_policy.allowed_commands:
            allowed = ", ".join(sorted(self._cmd_policy.allowed_commands))
            raise SecurityBlocked(f"[차단] '{cmd_name}'은 허용되지 않은 명령어입니다. 허용: {allowed}")

        return tokens

    # ──────────────────────────────────────────
    # Outbound HTTP guards (HARD default deny)
    # ──────────────────────────────────────────

    def _check_robots_txt(self, url: str) -> bool:
        """
        robots.txt 준수 여부 확인.
        
        Returns:
            True: robots.txt가 허용하거나 확인 불가능한 경우
            False: robots.txt가 명시적으로 차단한 경우
        """
        try:
            parsed = urlsplit(url)
            if not parsed.scheme or not parsed.netloc:
                # 유효하지 않은 URL은 robots.txt 확인 불가
                return True
            
            # robots.txt URL 생성
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            
            # RobotFileParser 사용
            rp = RobotFileParser()
            rp.set_url(robots_url)
            
            # 타임아웃 설정 (5초)
            rp.read()
            
            # User-agent: * 에 대한 접근 허용 여부 확인
            user_agent = "MellowLink/1.0"  # 기본 User-Agent
            can_fetch = rp.can_fetch(user_agent, url)
            
            if not can_fetch:
                logger.warning(
                    f"[SecurityManager] robots.txt blocked access to {url} "
                    f"(User-Agent: {user_agent})"
                )
                return False
            
            return True
            
        except Exception as e:
            # robots.txt 확인 실패 시 기본적으로 허용 (fail-open)
            # 보안상 차단하는 것이 더 안전하지만, 네트워크 오류 등으로 인한
            # false positive를 방지하기 위해 허용
            logger.debug(f"[SecurityManager] robots.txt check failed for {url}: {e}")
            return True

    def is_outbound_http_allowed(self, url: str) -> bool:
        """
        외부 HTTP 접근 허용 여부.
        - EASY: robots.txt 확인 후 허용
        - NORMAL: 기본 차단 (외부 HTTP 접근 비활성화)
        - HARD: 기본 차단. MELLOW_ALLOW_OUTBOUND=true 일 때만 허용
        
        robots.txt 준수: 모든 레벨에서 robots.txt를 확인하여 차단된 URL은 접근 불가.
        """
        # robots.txt 확인 (모든 레벨에서 적용)
        if not self._check_robots_txt(url):
            return False
        
        if self._level == "EASY":
            return True

        if self._level == "HARD":
            return _truthy_env(self._HARD_OUTBOUND_OVERRIDE_ENV)

        # NORMAL: 외부 HTTP 접근 차단
        return False

