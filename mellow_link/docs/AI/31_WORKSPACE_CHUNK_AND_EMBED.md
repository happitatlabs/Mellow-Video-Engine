# 워크스페이스 문서/코드 청킹 및 임베딩 저장

워크스페이스 내 문서·코드를 **청크 단위로 분할**하고 **임베딩해 저장**하며, **피드백을 기록**하는 파이프라인입니다.

## 단계 요약

| 단계 | 내용 | 목적 |
|------|------|------|
| 1. Preprocessing | 불필요한 공백/중복 줄바꿈/특수문자 제거 | 노이즈 제거 → 임베딩 정확도 상승 |
| 2. Structural/Semantic Splitting | `##` 제목, 단락, 코드블록·함수/클래스 경계로 분할 | 의미 단위 분할 → 정보 손실 최소화 |
| 3. Atomic Sizing | 청크를 500~1,000 토큰 내외로 조정 | 컨텍스트 윈도우 최적화, 정보 희석 방지 |
| 4. Contextual Overlap | 이전 청크 끝 10~20%를 다음 청크 시작에 중복 | 문맥 단절 방지 |
| 5. Embedding & 저장 | Ollama로 벡터화 후 SQLite 저장 | 검색/QA/도구 제작 기반 |
| 6. 피드백 기록 | 청크별 완료/실패/개선 아이디어 기록 | 자가발전 루프·다음 목표 개선 자료 |

## 파일 구성

- **`services/chunking_pipeline.py`**: 전처리, 구조 분할, 원자 크기, 오버랩 (설정 가능).
- **`infra/workspace_rag_store.py`**: 워크스페이스 청크·임베딩 DB 및 피드백 테이블.
- **`services/workspace_chunk_runner.py`**: 문서/폴더 반복 실행, 재시도, 임베딩 호출·저장·피드백 기록.

저장 위치:

- **DB**: `mellow_link/outputs/workspace_rag.db` (기본)
- **테이블**: `workspace_chunks`, `chunk_feedback`

## 사용 방법

### 1. 워크스페이스 폴더 전체 실행 (CLI)

```bash
# 프로젝트 루트에서
cd D:\AI_Project
python -m mellow_link.services.workspace_chunk_runner
```

기본값: `mellow_link/workspace/` 아래 `.md`, `.txt`, `.py`, `.json`, `.yaml`, `.yml`, `.rst`, `.html` 처리.

### 2. 옵션 조정 (점진적 청크 크기, Overlap 비율 등)

```bash
python -m mellow_link.services.workspace_chunk_runner \
  --root D:\AI_Project\mellow_link\workspace \
  --extensions "md,txt,py" \
  --min-tokens 500 \
  --max-tokens 1000 \
  --overlap-ratio 0.15 \
  --max-retries 2 \
  --db D:\AI_Project\mellow_link\outputs\workspace_rag.db
```

### 3. 코드에서 폴더/문서 단위 실행

```python
from pathlib import Path
from mellow_link.services.workspace_chunk_runner import (
    run_on_folder,
    process_one_document,
    get_rag_embedding_fn,
)

embed_fn = get_rag_embedding_fn()

# 폴더 전체
result = await run_on_folder(
    Path("mellow_link/workspace"),
    embed_fn,
    extensions={".md", ".txt", ".py"},
    min_tokens=500,
    max_tokens=1000,
    overlap_ratio=0.15,
    max_retries=2,
)
# result: files_processed, files_ok, files_failed, chunks_saved, errors

# 단일 문서만
ok, saved, msg = await process_one_document(
    Path("mellow_link/workspace/README.md"),
    embed_fn,
    min_tokens=500,
    max_tokens=1000,
    overlap_ratio=0.15,
)
```

### 4. 청킹만 사용 (임베딩 없이)

```python
from mellow_link.services.chunking_pipeline import (
    run_pipeline,
    run_pipeline_for_file,
    preprocess_text,
    structural_split,
    atomic_size_chunks,
    apply_overlap,
)

# 원문 → 청크 리스트
chunks = run_pipeline(
    raw_text,
    source_path="doc.md",
    min_tokens=500,
    max_tokens=1000,
    overlap_ratio=0.15,
)

# 파일 경로로 실행
chunks_meta, error = run_pipeline_for_file(Path("doc.md"), min_tokens=500, max_tokens=1000)
```

### 5. 저장된 청크·피드백 조회

```python
from mellow_link.infra.workspace_rag_store import (
    load_chunks,
    get_feedback,
    init_workspace_rag_db,
)

# 특정 문서 청크
chunks = load_chunks(source_path="D:/AI_Project/mellow_link/workspace/README.md")

# 전체 청크
all_chunks = load_chunks()

# 피드백 (실패/개선 아이디어 분석)
failed = get_feedback(status="failed")
completed = get_feedback(status="completed")
```

## 실패 시 동작

- **문서 단위**: `max_retries`(기본 2)만큼 재시도 후 실패 시 `chunk_feedback`에 `status='failed'`, `message`, `improvement_idea` 기록.
- **임베딩 실패**: 해당 청크는 건너뛰고, 피드백에 기록 후 나머지 청크는 계속 처리.

## 요구 사항

- **Ollama** 실행 중이며 `embedding` API 사용 가능 (기본: `nomic-embed-text`).
- 설정: `config/settings.py` 또는 환경변수 `MELLOW_OLLAMA_HOST`, `MELLOW_LLM_EMBEDDING_MODEL` 등.
