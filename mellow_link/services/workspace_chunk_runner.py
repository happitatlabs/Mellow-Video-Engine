"""
워크스페이스 문서/폴더 단위 청킹 → 임베딩 → 저장 → 피드백 기록

- 문서/폴더 단위 반복 실행
- 실패 시 자동 재시도 + 피드백 기록
- 점진적 청크 크기, Overlap 비율 조정 가능
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from mellow_link.services.chunking_pipeline import (
    ChunkWithMeta,
    run_pipeline_for_file,
    DEFAULT_MIN_CHUNK_TOKENS,
    DEFAULT_MAX_CHUNK_TOKENS,
    DEFAULT_OVERLAP_RATIO,
)
from mellow_link.infra.workspace_rag_store import (
    init_workspace_rag_db,
    save_chunks,
    record_feedback,
    load_chunks,
    get_feedback,
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# 설정 (점진적 조정 가능)
# -----------------------------------------------------------------------------
DEFAULT_EXTENSIONS = {".md", ".txt", ".py", ".json", ".yaml", ".yml", ".rst", ".html"}
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SEC = 1.0


async def embed_chunk(
    content: str,
    generate_embedding_fn: Callable[[str], Any],
) -> List[float]:
    """청크 텍스트 임베딩. generate_embedding_fn은 async (text) -> list[float]."""
    if not content or not content.strip():
        return []
    return await generate_embedding_fn(content)


async def process_one_document(
    file_path: Path,
    generate_embedding_fn: Callable[[str], Any],
    *,
    min_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    normalize_whitespace: bool = True,
    collapse_newlines: bool = True,
    remove_special: bool = False,
    use_semantic_breaks: bool = True,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
    db_path: Optional[Path] = None,
) -> Tuple[bool, int, str]:
    """
    단일 문서: 추출 → 청킹 → 임베딩 → 저장 → 피드백 기록.
    실패 시 max_retries만큼 재시도 후 피드백에 실패 기록.

    Returns: (success, chunks_saved, message)
    """
    source_path = str(file_path)
    last_error = ""

    for attempt in range(max_retries + 1):
        try:
            chunks_meta, extract_error = run_pipeline_for_file(
                file_path,
                min_tokens=min_tokens,
                max_tokens=max_tokens,
                overlap_ratio=overlap_ratio,
                normalize_whitespace=normalize_whitespace,
                collapse_newlines=collapse_newlines,
                remove_special=remove_special,
                use_semantic_breaks=use_semantic_breaks,
            )
            if extract_error:
                last_error = extract_error
                if attempt < max_retries:
                    await asyncio.sleep(retry_delay_sec)
                    continue
                record_feedback(
                    source_path,
                    -1,
                    "failed",
                    message=extract_error,
                    improvement_idea="파일 인코딩 또는 형식 확인",
                    db_path=db_path,
                )
                return False, 0, extract_error

            if not chunks_meta:
                record_feedback(
                    source_path,
                    -1,
                    "completed",
                    message="No chunks produced (empty or too small)",
                    db_path=db_path,
                )
                return True, 0, "No chunks produced"

            # 임베딩 생성
            chunks_to_save: List[Dict[str, Any]] = []
            for i, cm in enumerate(chunks_meta):
                emb = await embed_chunk(cm.content, generate_embedding_fn)
                if not emb and cm.content.strip():
                    record_feedback(
                        source_path,
                        cm.chunk_index,
                        "failed",
                        message="Embedding generation failed",
                        improvement_idea="Ollama/embedding 모델 확인",
                        db_path=db_path,
                    )
                    continue
                chunks_to_save.append({
                    "content": cm.content,
                    "chunk_index": cm.chunk_index,
                    "embedding": emb,
                    "topic_tag": cm.topic_tag or "",
                })
                record_feedback(
                    source_path,
                    cm.chunk_index,
                    "completed",
                    message=f"tokens={cm.token_count}",
                    db_path=db_path,
                )

            if not chunks_to_save:
                record_feedback(
                    source_path,
                    -1,
                    "failed",
                    message="All chunk embeddings failed",
                    improvement_idea="Embedding API 확인",
                    db_path=db_path,
                )
                return False, 0, "All chunk embeddings failed"

            saved = save_chunks(source_path, chunks_to_save, db_path=db_path)
            return True, saved, f"Saved {saved} chunks"

        except Exception as e:
            last_error = str(e)
            logger.exception("[WorkspaceChunk] Error processing %s (attempt %s)", file_path, attempt + 1)
            if attempt < max_retries:
                await asyncio.sleep(retry_delay_sec)
                continue
            record_feedback(
                source_path,
                -1,
                "failed",
                message=last_error,
                improvement_idea="재시도 또는 로그 확인",
                db_path=db_path,
            )
            return False, 0, last_error

    record_feedback(
        source_path,
        -1,
        "failed",
        message=last_error,
        improvement_idea="재시도 횟수 초과",
        db_path=db_path,
    )
    return False, 0, last_error


def collect_document_paths(
    root: Path,
    extensions: Optional[Set[str]] = None,
    exclude_dirs: Optional[Set[str]] = None,
) -> List[Path]:
    """root 아래에서 확장자에 해당하는 파일 목록 수집. exclude_dirs 폴더는 제외."""
    ext = extensions or DEFAULT_EXTENSIONS
    exclude = exclude_dirs or {".git", "__pycache__", "node_modules", ".venv", "venv"}
    out: List[Path] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if any(part in exclude for part in p.parts):
                continue
            if p.suffix.lower() in ext:
                out.append(p)
    except OSError as e:
        logger.warning("[WorkspaceChunk] list error under %s: %s", root, e)
    return sorted(out)


async def run_on_folder(
    folder_path: Path,
    generate_embedding_fn: Callable[[str], Any],
    *,
    extensions: Optional[Set[str]] = None,
    exclude_dirs: Optional[Set[str]] = None,
    min_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay_sec: float = DEFAULT_RETRY_DELAY_SEC,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    폴더 내 문서 단위로 반복: process_one_document 호출 후 집계 결과 반환.
    """
    init_workspace_rag_db(db_path=db_path)
    paths = collect_document_paths(folder_path, extensions=extensions, exclude_dirs=exclude_dirs)

    total_ok = 0
    total_fail = 0
    total_chunks = 0
    errors: List[Tuple[str, str]] = []

    for fp in paths:
        ok, saved, msg = await process_one_document(
            fp,
            generate_embedding_fn,
            min_tokens=min_tokens,
            max_tokens=max_tokens,
            overlap_ratio=overlap_ratio,
            max_retries=max_retries,
            retry_delay_sec=retry_delay_sec,
            db_path=db_path,
        )
        if ok:
            total_ok += 1
            total_chunks += saved
        else:
            total_fail += 1
            errors.append((str(fp), msg))

    return {
        "folder": str(folder_path),
        "files_processed": len(paths),
        "files_ok": total_ok,
        "files_failed": total_fail,
        "chunks_saved": total_chunks,
        "errors": errors,
    }


def get_rag_embedding_fn():
    """RAG 서비스의 generate_embedding을 래핑한 async (text) -> list[float] 반환."""
    from mellow_link.services.rag_service import generate_embedding
    from mellow_link.config.settings import get_settings

    settings = get_settings()
    base_url = settings.ollama_url
    model = getattr(settings, "embedding_model", "nomic-embed-text") or "nomic-embed-text"

    async def _embed(text: str):
        return await generate_embedding(text, model=model, base_url=base_url)

    return _embed


# -----------------------------------------------------------------------------
# CLI / 진입점
# -----------------------------------------------------------------------------

async def main_async(
    workspace_root: Optional[Path] = None,
    extensions: Optional[str] = None,
    min_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    max_retries: int = DEFAULT_MAX_RETRIES,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    워크스페이스 루트에서 청킹 파이프라인 실행.
    extensions: 쉼표 구분 예 "md,txt,py"
    """
    from mellow_link.core.workspace_sandbox import get_workspace_root

    root = workspace_root or get_workspace_root()
    ext_set = None
    if extensions:
        ext_set = {"." + s.strip().lstrip(".") for s in extensions.split(",") if s.strip()}
    if not ext_set:
        ext_set = DEFAULT_EXTENSIONS

    embed_fn = get_rag_embedding_fn()
    return await run_on_folder(
        root,
        embed_fn,
        extensions=ext_set,
        min_tokens=min_tokens,
        max_tokens=max_tokens,
        overlap_ratio=overlap_ratio,
        max_retries=max_retries,
        db_path=db_path,
    )


if __name__ == "__main__":
    import argparse
    import json

    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description="워크스페이스 문서 청킹 → 임베딩 → 저장 → 피드백")
    p.add_argument("--root", type=Path, default=None, help="워크스페이스 루트 (기본: workspace)")
    p.add_argument("--extensions", type=str, default="md,txt,py,json,yaml,yml,rst,html", help="처리 확장자 (쉼표 구분)")
    p.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_CHUNK_TOKENS, help="청크 최소 토큰 수")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_CHUNK_TOKENS, help="청크 최대 토큰 수")
    p.add_argument("--overlap-ratio", type=float, default=DEFAULT_OVERLAP_RATIO, help="오버랩 비율 (0.1~0.2 권장)")
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES, help="문서당 최대 재시도")
    p.add_argument("--db", type=Path, default=None, help="workspace_rag DB 경로")
    args = p.parse_args()

    result = asyncio.run(
        main_async(
            workspace_root=args.root,
            extensions=args.extensions,
            min_tokens=args.min_tokens,
            max_tokens=args.max_tokens,
            overlap_ratio=args.overlap_ratio,
            max_retries=args.max_retries,
            db_path=args.db,
        )
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
