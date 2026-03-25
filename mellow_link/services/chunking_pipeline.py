"""
워크스페이스 문서/코드 청크 단위 분할 파이프라인

목표: 노이즈 제거 → 구조/의미 단위 분할 → 원자 크기 조정 → 맥락 오버랩 → 임베딩/저장용 청크 산출

단계:
  1. Preprocessing (데이터 세탁)
  2. Structural/Semantic Splitting (제목, 단락, 의미 변환 지점)
  3. Atomic Sizing (500~1000 토큰)
  4. Contextual Overlap (이전 청크 끝 10~20% 중복)
"""

import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# 기본 상수 (점진적 조정 가능)
# -----------------------------------------------------------------------------
DEFAULT_MIN_CHUNK_TOKENS = 500
DEFAULT_MAX_CHUNK_TOKENS = 1000
DEFAULT_OVERLAP_RATIO = 0.15  # 15%
# 특수문자 제거 시 유지할 문자 (코드/식별자용)
KEEP_CHARS_PATTERN = re.compile(r"[^\w\s\u3130-\u318f\uac00-\ud7af\u4e00-\u9fff.,;:!?()\[\]{}<>/\\\-+=*&|#@\"'`\n\t]")


# -----------------------------------------------------------------------------
# 1. Preprocessing (데이터 세탁)
# -----------------------------------------------------------------------------

def preprocess_text(
    text: str,
    normalize_whitespace: bool = True,
    collapse_newlines: bool = True,
    remove_special: bool = False,
) -> str:
    """
    불필요한 공백/줄바꿈 제거, 선택적 특수문자 제거.
    노이즈 제거 → 임베딩 정확도 상승.
    """
    if not text or not isinstance(text, str):
        return ""

    if normalize_whitespace:
        # 연속 공백을 하나로 (탭/스페이스 통일)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"^\s+|\s+$", "", text, flags=re.MULTILINE)

    if collapse_newlines:
        # 연속 줄바꿈을 최대 2개로 (단락 구분 유지)
        text = re.sub(r"\n{3,}", "\n\n", text)

    if remove_special:
        text = KEEP_CHARS_PATTERN.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()

    return text


# -----------------------------------------------------------------------------
# 2. Structural / Semantic Splitting
# -----------------------------------------------------------------------------

def _split_by_headers_and_paragraphs(text: str) -> List[str]:
    """
    Markdown/문서: ## 제목, 단락(빈 줄), 코드블록 경계로 큰 단위 분할.
    FSM처럼 '제목' -> '본문' 상태 전환 지점을 구분.
    """
    if not text.strip():
        return []

    # 코드블록 경계 보존 (``` ... ```)
    parts: List[str] = []
    current = []
    in_fence = False
    fence_char = ""
    lines = text.split("\n")

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Markdown 헤더 (##, ###, #### 등)
        if re.match(r"^#{1,6}\s", line):
            if current:
                parts.append("\n".join(current))
                current = []
            current.append(line)
            i += 1
            continue

        # Fence 시작/종료
        if stripped.startswith("```"):
            if not in_fence:
                if current:
                    parts.append("\n".join(current))
                    current = []
                fence_char = stripped[:3]
                in_fence = True
                current.append(line)
            elif stripped.startswith(fence_char) or stripped == "```":
                current.append(line)
                in_fence = False
                parts.append("\n".join(current))
                current = []
            else:
                current.append(line)
            i += 1
            continue

        if in_fence:
            current.append(line)
            i += 1
            continue

        # 빈 줄 = 단락 경계
        if stripped == "":
            if current:
                parts.append("\n".join(current))
                current = []
            i += 1
            continue

        current.append(line)
        i += 1

    if current:
        parts.append("\n".join(current))

    return [p.strip() for p in parts if p.strip()]


def _split_by_semantic_breaks(segment: str) -> List[str]:
    """
    한 세그먼트 내 추가 의미 변환: 함수/클래스 정의, 주석 블록 경계.
    """
    out: List[str] = []
    # 파이썬: def, class, """ 또는 ''' 로 시작하는 독스트링
    pattern = re.compile(
        r"^(def\s+\w+|class\s+\w+|#{3,}\s*[-]+\s*|\"\"\"|\'\'\')",
        re.MULTILINE,
    )
    last_end = 0
    for m in pattern.finditer(segment):
        if m.start() > last_end:
            chunk = segment[last_end : m.start()].strip()
            if chunk:
                out.append(chunk)
        last_end = m.start()
    if last_end < len(segment):
        chunk = segment[last_end:].strip()
        if chunk:
            out.append(chunk)
    return out if out else [segment] if segment.strip() else []


def structural_split(text: str, use_semantic_breaks: bool = True) -> List[str]:
    """
    제목(##), 단락, 의미 변환 지점 기준으로 큰 단위 청크 생성.
    정보 손실 최소화.
    """
    segments = _split_by_headers_and_paragraphs(text)
    if not use_semantic_breaks:
        return segments

    result: List[str] = []
    for seg in segments:
        sub = _split_by_semantic_breaks(seg)
        result.extend(sub)
    return result


# -----------------------------------------------------------------------------
# Token counting (tiktoken fallback to heuristic)
# -----------------------------------------------------------------------------

def get_token_counter():
    """tiktoken 사용 가능 시 토큰 수 반환 함수, 아니면 휴리스틱(대략 4자당 1토큰)."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        def count(s: str) -> int:
            return len(enc.encode(s))
        return count
    except Exception as e:
        logger.warning("[Chunking] tiktoken not available, using char/4 heuristic: %s", e)
        def count(s: str) -> int:
            return max(1, len(s) // 4)
        return count


_token_counter = None


def count_tokens(text: str) -> int:
    global _token_counter
    if _token_counter is None:
        _token_counter = get_token_counter()
    return _token_counter(text)


# -----------------------------------------------------------------------------
# 3. Atomic Sizing (500~1000 토큰)
# -----------------------------------------------------------------------------

def atomic_size_chunks(
    segments: List[str],
    min_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
) -> List[str]:
    """
    각 세그먼트를 min~max 토큰 범위로 쪼갬.
    이미 작은 세그먼트는 그대로, 큰 것은 문장/줄 단위로 분할.
    """
    if max_tokens < min_tokens:
        max_tokens = min_tokens

    result: List[str] = []
    for seg in segments:
        n = count_tokens(seg)
        if n <= max_tokens:
            if seg.strip():
                result.append(seg.strip())
            continue

        # 문장/줄 경계로 자르기
        lines = seg.split("\n")
        current: List[str] = []
        current_tokens = 0

        for line in lines:
            line_tokens = count_tokens(line)
            if current_tokens + line_tokens > max_tokens and current:
                chunk = "\n".join(current).strip()
                if chunk:
                    result.append(chunk)
                current = [line]
                current_tokens = line_tokens
            else:
                current.append(line)
                current_tokens += line_tokens

        if current:
            chunk = "\n".join(current).strip()
            if chunk:
                result.append(chunk)

    return result


# -----------------------------------------------------------------------------
# 4. Contextual Overlap (맥락 보험)
# -----------------------------------------------------------------------------

def apply_overlap(
    chunks: List[str],
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    min_overlap_tokens: int = 50,
) -> List[str]:
    """
    이전 청크 끝 10~20%를 다음 청크 시작에 중복 포함.
    문맥 단절 방지.
    """
    if len(chunks) <= 1 or overlap_ratio <= 0:
        return chunks

    result: List[str] = []
    for i, chunk in enumerate(chunks):
        if i == 0:
            result.append(chunk)
            continue

        prev = result[-1]
        prev_tokens = count_tokens(prev)
        overlap_tokens = max(
            min_overlap_tokens,
            int(prev_tokens * overlap_ratio),
        )
        # prev 끝에서 overlap_tokens만큼 추출 (단순 휴리스틱: 문자 기준)
        approx_chars = overlap_tokens * 4
        overlap_text = prev[-approx_chars:].strip() if len(prev) > approx_chars else prev.strip()
        if overlap_text and not chunk.strip().startswith(overlap_text[:50]):
            new_chunk = overlap_text + "\n\n" + chunk.strip()
        else:
            new_chunk = chunk.strip()
        result.append(new_chunk)

    return result


# -----------------------------------------------------------------------------
# 통합: 원문 → 전처리 → 구조 분할 → 원자 크기 → 오버랩
# -----------------------------------------------------------------------------

@dataclass
class ChunkWithMeta:
    """청크 + 메타데이터 (저장/태깅용)."""
    content: str
    chunk_index: int
    source_path: str = ""
    topic_tag: str = ""
    token_count: int = 0


def run_pipeline(
    text: str,
    source_path: str = "",
    min_tokens: int = DEFAULT_MIN_CHUNK_TOKENS,
    max_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    overlap_ratio: float = DEFAULT_OVERLAP_RATIO,
    normalize_whitespace: bool = True,
    collapse_newlines: bool = True,
    remove_special: bool = False,
    use_semantic_breaks: bool = True,
) -> List[ChunkWithMeta]:
    """
    전처리 → 구조 분할 → 원자 크기 → 오버랩 적용 후 ChunkWithMeta 리스트 반환.
    """
    if not text or not text.strip():
        return []

    cleaned = preprocess_text(
        text,
        normalize_whitespace=normalize_whitespace,
        collapse_newlines=collapse_newlines,
        remove_special=remove_special,
    )
    if not cleaned.strip():
        return []

    segments = structural_split(cleaned, use_semantic_breaks=use_semantic_breaks)
    if not segments:
        segments = [cleaned]

    sized = atomic_size_chunks(segments, min_tokens=min_tokens, max_tokens=max_tokens)
    if not sized:
        sized = [cleaned]

    overlapped = apply_overlap(sized, overlap_ratio=overlap_ratio)

    result: List[ChunkWithMeta] = []
    for i, content in enumerate(overlapped):
        if not content.strip():
            continue
        result.append(
            ChunkWithMeta(
                content=content.strip(),
                chunk_index=i,
                source_path=source_path,
                topic_tag="",  # 호출측에서 채우거나 이후 추출
                token_count=count_tokens(content),
            )
        )
    return result


def run_pipeline_for_file(
    file_path: Path,
    encoding: str = "utf-8",
    **kwargs,
) -> Tuple[List[ChunkWithMeta], str]:
    """
    파일에서 텍스트 읽기 → run_pipeline 실행.
    Returns: (chunks, error_message). error_message가 비어있으면 성공.
    """
    try:
        raw = file_path.read_text(encoding=encoding, errors="replace")
    except Exception as e:
        return [], str(e)

    source = str(file_path)
    chunks = run_pipeline(raw, source_path=source, **kwargs)
    return chunks, ""
