# 장문 입력 큐 작업 처리 설계안

## 현재 시스템 분석

### ✅ 이미 있는 것들

1. **Orchestrator PriorityQueue** (`core/orchestrator.py`)
   - `_task_queue: asyncio.PriorityQueue` (최대 100개 작업)
   - 우선순위 기반 작업 스케줄링
   - TaskPriority: CRITICAL, HIGH, NORMAL, LOW

2. **텍스트 청킹 기능**
   - `services/chunking_pipeline.py`: 구조적/의미적 분할
   - `services/rag_service.py`: `chunk_text()` 함수
   - 문장 경계, 오버랩 지원

3. **SchedulerService** (`core/scheduler_service.py`)
   - 시간 기반 스케줄링
   - 백그라운드 작업 실행

### ❌ 부족한 것들

1. **장문 → 큐 작업 자동 변환 로직**
2. **작업 간 의존성 관리**
3. **작업 그룹 추적 및 결과 통합**
4. **진행 상황 모니터링**

---

## 필요한 구성 요소

### 1. 작업 분할 전략 (Task Chunking Strategy)

```python
# mellow_link/core/task_chunker.py (신규)

class TaskChunker:
    """
    장문 입력을 큐 작업으로 분할하는 전략 클래스
    """
    
    def chunk_long_input(
        self,
        long_text: str,
        strategy: str = "semantic",  # "semantic" | "fixed" | "sentence"
        max_chunk_size: int = 2000,  # 토큰 또는 문자 수
        overlap: int = 200
    ) -> List[TaskChunk]:
        """
        장문을 의미 단위로 분할
        
        Returns:
            List[TaskChunk]: 각 청크의 메타데이터 포함
        """
        pass
```

**전략 옵션:**
- `semantic`: 의미 단위 분할 (단락, 섹션)
- `fixed`: 고정 크기 분할
- `sentence`: 문장 단위 분할

### 2. 작업 그룹 관리자 (Task Group Manager)

```python
# mellow_link/core/task_group_manager.py (신규)

class TaskGroupManager:
    """
    관련된 여러 작업을 그룹으로 관리
    """
    
    def create_task_group(
        self,
        chunks: List[TaskChunk],
        execution_mode: str = "sequential"  # "sequential" | "parallel" | "mixed"
    ) -> TaskGroup:
        """
        청크들을 작업 그룹으로 변환
        
        Args:
            execution_mode:
                - sequential: 순차 실행 (이전 작업 완료 후 다음 시작)
                - parallel: 병렬 실행 (모든 작업 동시 시작)
                - mixed: 의존성 기반 (일부는 병렬, 일부는 순차)
        """
        pass
    
    async def submit_group_to_queue(
        self,
        group: TaskGroup,
        orchestrator: Orchestrator
    ) -> str:
        """
        작업 그룹을 큐에 제출
        
        Returns:
            group_id: 작업 그룹 추적용 ID
        """
        pass
```

### 3. 작업 의존성 관리 (Task Dependency)

```python
# mellow_link/core/task_dependency.py (신규)

@dataclass
class TaskDependency:
    """
    작업 간 의존성 정의
    """
    task_id: str
    depends_on: List[str]  # 선행 작업 ID 리스트
    wait_for_all: bool = True  # 모든 선행 작업 완료 대기 여부
```

**의존성 타입:**
- **순차 의존성**: Task 2는 Task 1 완료 후 시작
- **병렬 그룹**: Task 2, 3, 4는 Task 1 완료 후 동시 시작
- **조건부 의존성**: Task 3은 Task 1 또는 Task 2 중 하나 완료 시 시작

### 4. 작업 상태 추적 (Task Status Tracking)

```python
# mellow_link/infra/memory_database.py (확장)

# 새로운 테이블 추가
CREATE TABLE task_groups (
    id TEXT PRIMARY KEY,
    user_input TEXT NOT NULL,
    total_chunks INTEGER NOT NULL,
    completed_chunks INTEGER DEFAULT 0,
    status TEXT NOT NULL,  -- PENDING, PROCESSING, COMPLETED, FAILED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE task_chunks (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_content TEXT NOT NULL,
    status TEXT NOT NULL,  -- PENDING, QUEUED, PROCESSING, COMPLETED, FAILED
    result TEXT,
    error_message TEXT,
    depends_on TEXT,  -- JSON array of task IDs
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (group_id) REFERENCES task_groups(id)
);
```

### 5. 결과 통합 (Result Aggregation)

```python
# mellow_link/core/result_aggregator.py (신규)

class ResultAggregator:
    """
    여러 작업의 결과를 통합
    """
    
    async def aggregate_results(
        self,
        group_id: str,
        aggregation_strategy: str = "concatenate"  # "concatenate" | "summarize" | "merge"
    ) -> str:
        """
        작업 그룹의 모든 결과를 통합
        
        Args:
            aggregation_strategy:
                - concatenate: 순서대로 연결
                - summarize: LLM으로 요약
                - merge: 구조화된 데이터 병합
        """
        pass
```

---

## 구현 시나리오

### 시나리오 1: 순차 처리 (Sequential)

```
장문 입력 (5000자)
    ↓
[Chunker] 3개 청크로 분할
    ↓
[TaskGroupManager] 순차 의존성 설정
    Task 1 (청크 1) → Task 2 (청크 2) → Task 3 (청크 3)
    ↓
[Orchestrator] 큐에 순차 제출
    ↓
[ResultAggregator] 결과 순서대로 연결
```

**사용 사례:**
- 장문 문서 분석 (이전 컨텍스트 필요)
- 단계별 작업 (이전 단계 결과 사용)

### 시나리오 2: 병렬 처리 (Parallel)

```
장문 입력 (5000자)
    ↓
[Chunker] 5개 독립 청크로 분할
    ↓
[TaskGroupManager] 병렬 실행 설정
    Task 1, 2, 3, 4, 5 동시 시작
    ↓
[Orchestrator] 큐에 병렬 제출
    ↓
[ResultAggregator] 모든 결과 수집 후 통합
```

**사용 사례:**
- 독립적인 작업들 (서로 의존성 없음)
- 빠른 처리 필요 시

### 시나리오 3: 혼합 처리 (Mixed)

```
장문 입력 (10000자)
    ↓
[Chunker] 6개 청크로 분할
    ↓
[TaskGroupManager] 의존성 분석
    Task 1 완료 → Task 2, 3 병렬 → Task 4, 5 병렬 → Task 6
    ↓
[Orchestrator] 의존성 기반 큐 제출
    ↓
[ResultAggregator] 의존성 순서대로 통합
```

---

## API 설계

### 엔드포인트 추가

```python
# mellow_link/main.py

@app.post("/chat/queue-long-text", tags=["Chat"])
async def queue_long_text(
    request: LongTextRequest,
    http_request: Request
):
    """
    장문 입력을 큐 작업으로 분할하여 처리
    
    Request Body:
    {
        "text": "장문 텍스트...",
        "chunking_strategy": "semantic",  # optional
        "execution_mode": "sequential",    # optional
        "max_chunk_size": 2000,           # optional
        "aggregation_strategy": "concatenate"  # optional
    }
    
    Response:
    {
        "group_id": "uuid",
        "total_chunks": 5,
        "status": "PENDING",
        "estimated_time": "5분"
    }
    """
    pass

@app.get("/chat/queue-status/{group_id}", tags=["Chat"])
async def get_queue_status(group_id: str):
    """
    작업 그룹 진행 상황 조회
    
    Response:
    {
        "group_id": "uuid",
        "status": "PROCESSING",
        "total_chunks": 5,
        "completed_chunks": 3,
        "progress": 60.0,
        "chunks": [
            {"id": "...", "status": "COMPLETED", "result": "..."},
            {"id": "...", "status": "PROCESSING", "result": null}
        ]
    }
    """
    pass

@app.get("/chat/queue-result/{group_id}", tags=["Chat"])
async def get_queue_result(group_id: str):
    """
    작업 그룹 최종 결과 조회
    
    Response:
    {
        "group_id": "uuid",
        "status": "COMPLETED",
        "aggregated_result": "통합된 결과...",
        "chunks": [...]
    }
    """
    pass
```

---

## 필요한 데이터 구조

### TaskChunk

```python
@dataclass
class TaskChunk:
    """작업 청크 메타데이터"""
    id: str
    content: str
    index: int
    token_count: int
    metadata: Dict[str, Any]  # 원본 위치, 컨텍스트 등
```

### TaskGroup

```python
@dataclass
class TaskGroup:
    """작업 그룹"""
    id: str
    user_input: str
    chunks: List[TaskChunk]
    execution_mode: str
    dependencies: List[TaskDependency]
    status: str
    created_at: datetime
```

---

## 구현 우선순위

### Phase 1: 기본 기능 (1주)
1. ✅ TaskChunker 구현
2. ✅ TaskGroupManager 기본 구현
3. ✅ DB 스키마 추가 (task_groups, task_chunks)
4. ✅ 순차 처리만 지원

### Phase 2: 고급 기능 (2주)
1. ✅ 병렬 처리 지원
2. ✅ 의존성 관리
3. ✅ 진행 상황 모니터링 API
4. ✅ 결과 통합

### Phase 3: 최적화 (1주)
1. ✅ 스마트 청킹 (의미 단위)
2. ✅ 동적 청크 크기 조정
3. ✅ 실패 시 재시도 로직
4. ✅ 부분 결과 조기 반환

---

## 고려사항

### 1. 큐 용량 관리
- 현재 Orchestrator 큐 최대 크기: 100개
- 장문이 50개 이상 청크로 나뉘면 큐 포화 가능
- **해결책**: 큐 크기 동적 확장 또는 청크 크기 조정

### 2. 메모리 관리
- 모든 청크를 메모리에 보관하면 메모리 부족 가능
- **해결책**: DB에 저장하고 필요한 것만 로드

### 3. 타임아웃 처리
- 일부 작업이 실패하면 전체 그룹 실패?
- **해결책**: 부분 실패 허용 + 재시도 정책

### 4. 사용자 경험
- 장문 처리 중 사용자는 어떻게 진행 상황을 확인?
- **해결책**: WebSocket으로 실시간 진행 상황 전송

---

## 결론

**장문을 큐 작업으로 나누려면:**

1. ✅ **큐 시스템**: 이미 있음 (Orchestrator PriorityQueue)
2. ❌ **청킹 전략**: 기본 기능은 있으나 작업 변환 로직 필요
3. ❌ **작업 그룹 관리**: 신규 구현 필요
4. ❌ **의존성 관리**: 신규 구현 필요
5. ❌ **상태 추적**: DB 스키마 확장 필요
6. ❌ **결과 통합**: 신규 구현 필요

**최소 구현으로 시작:**
- 순차 처리만 지원하는 간단한 TaskChunker + TaskGroupManager
- DB에 작업 그룹/청크 저장
- API로 진행 상황 조회

이렇게 하면 기본적인 장문 큐 처리가 가능합니다.
