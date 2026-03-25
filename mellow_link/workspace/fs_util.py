"""
Workspace FileSystem Utilities — 에이전트용 통합 저장소 인터페이스.

인터페이스 기반 구조: BaseStorageManager(ABC)를 구현한 LocalFSManager가 로컬 파일 시스템을 담당하며,
향후 WebFetcher 등 다른 저장소를 끼워 넣을 수 있도록 설계되어 있다.
모든 공통 메서드는 비동기(async)로 정의되어 네트워크 지연 대응 및 확장성을 확보한다.

[확장 가이드]
- 새로운 저장소 타입(예: WebFetcher)은 BaseStorageManager를 상속해 read/write/list/exists/delete를 구현한다.
- 에이전트는 get_storage()로 인터페이스만 바라보고 사용하며, 설정만 바꾸면 다른 구현체가 주입된다.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional, Union

# workspace 기준 디렉터리 (이 파일이 workspace/ 안에 있으므로 부모가 workspace)
_WORKSPACE_DIR = Path(__file__).resolve().parent

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# 추상 베이스 클래스 (인터페이스)
# ═══════════════════════════════════════════════


class BaseStorageManager(ABC):
    """
    저장소 접근 공통 인터페이스.
    로컬 FS, 웹 스크랩(WebFetcher) 등 구체 구현은 이 인터페이스를 상속한다.
    모든 메서드는 비동기로 정의하여 네트워크 지연 대응 및 확장성을 확보한다.
    """

    @abstractmethod
    async def read(self, path: str, **kwargs: object) -> str:
        """경로(또는 식별자)의 내용을 읽어 문자열로 반환한다."""
        ...

    @abstractmethod
    async def write(self, path: str, content: str, **kwargs: object) -> None:
        """경로에 내용을 쓴다."""
        ...

    @abstractmethod
    async def list(self, path: str = "", **kwargs: object) -> List[str]:
        """경로 아래 항목 목록을 반환한다."""
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """경로가 존재하는지 여부를 반환한다."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> bool:
        """경로를 삭제한다. 성공 시 True, 실패 시 False."""
        ...


# ═══════════════════════════════════════════════
# 로컬 파일 시스템 구현 (sandbox 검증 유지)
# ═══════════════════════════════════════════════


class LocalFSManager(BaseStorageManager):
    """
    로컬 파일 시스템용 BaseStorageManager 구현.
    workspace(sandbox) 기준 경로 검증을 유지하며, 모든 I/O는 asyncio.to_thread로 비동기화한다.
    """

    def __init__(self, base_path: Optional[Path] = None):
        self._base = Path(base_path) if base_path is not None else _WORKSPACE_DIR

    def _resolve(self, path: Union[str, Path]) -> Path:
        """base 기준으로 경로를 해석하고, base 밖으로 나가면 PermissionError."""
        p = self._base / path if path else self._base
        resolved = p.resolve()
        try:
            resolved.relative_to(self._base.resolve())
        except ValueError:
            raise PermissionError(f"경로가 sandbox({self._base}) 밖으로 나갑니다: {resolved}")
        return resolved

    async def read(
        self,
        path: str,
        encoding: str = "utf-8",
        max_size_bytes: Optional[int] = 2 * 1024 * 1024,
        **kwargs: object,
    ) -> str:
        full = self._resolve(path)
        if not await asyncio.to_thread(full.exists):
            return ""
        if not await asyncio.to_thread(full.is_file):
            return ""

        def _do_read() -> str:
            if max_size_bytes is not None and full.stat().st_size > max_size_bytes:
                return f"[파일이 너무 큽니다. 최대 {max_size_bytes} bytes.]"
            return full.read_text(encoding=encoding, errors="replace")

        try:
            return await asyncio.to_thread(_do_read)
        except (PermissionError, OSError, UnicodeDecodeError) as e:
            return f"[읽기 오류: {e!s}]"

    async def write(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        **kwargs: object,
    ) -> None:
        full = self._resolve(path)
        await asyncio.to_thread(full.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(full.write_text, content, encoding=encoding)

    async def list(
        self,
        path: str = "",
        relative: bool = True,
        **kwargs: object,
    ) -> List[str]:
        root = self._resolve(path)
        if not await asyncio.to_thread(root.exists) or not await asyncio.to_thread(root.is_dir):
            return []

        def _do_list() -> List[str]:
            out: List[str] = []
            try:
                for p in root.rglob("*"):
                    if relative:
                        try:
                            out.append(str(p.relative_to(root)))
                        except ValueError:
                            out.append(p.name)
                    else:
                        out.append(str(p.resolve()))
            except (PermissionError, OSError):
                pass
            return sorted(out)

        return await asyncio.to_thread(_do_list)

    async def exists(self, path: str) -> bool:
        full = self._resolve(path)
        return await asyncio.to_thread(full.exists)

    async def delete(self, path: str) -> bool:
        full = self._resolve(path)
        if not await asyncio.to_thread(full.exists):
            return False
        if await asyncio.to_thread(full.is_dir):
            return False
        try:
            await asyncio.to_thread(full.unlink)
            return True
        except OSError:
            return False

    # ─── 기존 호환·편의 메서드 ───

    async def list_tree(self, base_path: str = "", prefix: str = "") -> List[str]:
        """디렉터리 트리를 텍스트 트리 형태로 나열한다."""

        def _walk(current: Path, pre: str, acc: List[str]) -> None:
            try:
                entries = sorted(current.iterdir())
            except (PermissionError, OSError):
                return
            for i, entry in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                acc.append(pre + connector + entry.name)
                if entry.is_dir():
                    ext = "    " if is_last else "│   "
                    _walk(entry, pre + ext, acc)

        def _do_tree() -> List[str]:
            root = self._resolve(base_path)
            if not root.exists() or not root.is_dir():
                return []
            lines: List[str] = []
            _walk(root, prefix, lines)
            return lines

        return await asyncio.to_thread(_do_tree)

    # 동기 래퍼 (기존 sync 호출자용; 이미 이벤트 루프 안이면 await 사용 권장)
    def read_sync(
        self,
        path: str,
        encoding: str = "utf-8",
        max_size_bytes: Optional[int] = 2 * 1024 * 1024,
    ) -> str:
        return asyncio.run(self.read(path, encoding=encoding, max_size_bytes=max_size_bytes))

    def list_sync(self, path: str = "", relative: bool = True) -> List[str]:
        return asyncio.run(self.list(path=path, relative=relative))


# ═══════════════════════════════════════════════
# 팩토리 및 의존성 주입
# ═══════════════════════════════════════════════

_default_storage: Optional[BaseStorageManager] = None


def get_storage(base_path: Optional[Path] = None) -> BaseStorageManager:
    """
    저장소 인터페이스 인스턴스를 반환한다.
    현재는 LocalFSManager가 주입되며, 설정만 바꾸면 WebFetcher 등 다른 구현체로 교체 가능하다.
    """
    global _default_storage
    if base_path is not None:
        return LocalFSManager(base_path=base_path)
    if _default_storage is None:
        _default_storage = LocalFSManager()
        _run_verification_log()
    return _default_storage


def _run_verification_log() -> None:
    """리팩터링 후 주요 기능 정상 동작 확인 로그 (신뢰도 태그)."""
    async def _check() -> None:
        storage = _default_storage
        if not isinstance(storage, LocalFSManager):
            return
        base = storage._base
        test_rel = "fs_util.py"
        if not (base / test_rel).exists():
            logger.info("[fs_util] ⚠️ possible: workspace 루트에 fs_util.py 없음(다른 경로면 정상).")
            return
        content = await storage.read(test_rel, max_size_bytes=500)
        if content.startswith("[읽기 오류"):
            logger.warning("[fs_util] ⚠️ possible: read() 검증 시 읽기 오류.")
        else:
            logger.info("[fs_util] ✅ verified: 로컬 파일 읽기/쓰기 비동기 전환 성공.")
        logger.info("[fs_util] ⚠️ possible: 향후 WebSource 클래스 추가 시 read(url) 형태의 인터페이스 확장 준비 완료.")

    try:
        asyncio.run(_check())
    except Exception as e:
        logger.warning("[fs_util] ⚠️ possible: 검증 로그 실행 중 예외: %s", e)


# ─── 하위 호환: 기존 API ───

def get_fs_manager(base_path: Optional[Path] = None) -> LocalFSManager:
    """기본 로컬 저장소 인스턴스 반환. get_storage()와 동일한 인스턴스를 LocalFSManager로 반환."""
    return get_storage(base_path)  # type: ignore[return-value]


# 문서/프롬프트에서 참조되는 이름 유지
FileSystemManager = LocalFSManager
