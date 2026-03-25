"""
Memory Database - 영속적 경험 메모리 시스템의 DB 인터페이스

SQLite를 사용하여 experience_ledger와 tool_stats 테이블을 관리합니다.
- experience_ledger: 모든 태스크의 성패와 교훈 기록
- tool_stats: 도구 효율 통계 추적
"""

import sqlite3
import json
import logging
import hashlib
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


# =============================================================================
# 데이터 구조
# =============================================================================

@dataclass
class ExperienceRecord:
    """경험 장부 레코드."""
    id: str  # UUID 기반 고유 식별자
    task_intent: str  # 사용자 의도 요약
    task_hash: str  # 유사 작업 식별을 위한 특징값
    context_summary: str  # 실행 당시의 핵심 상황 및 제약사항
    action_steps: str  # ReAct 루프 전체 시퀀스 (JSON String)
    final_outcome: str  # 최종 결과물
    is_success: int  # 성공 여부 (0: 실패, 1: 성공)
    critique_tag: Optional[str] = None  # 실패 원인 태그 (#API_Error, #Logic_Error 등)
    lessons_learned: Optional[str] = None  # 핵심 교훈
    embedding: Optional[bytes] = None  # 시맨틱 검색용 벡터 데이터 (추후 확장용)
    created_at: Optional[datetime] = None  # 기록 생성 일시
    latency_ms: Optional[float] = None  # 요청 처리 지연(ms)
    used_tools: Optional[str] = None  # 사용 도구 목록 JSON
    error_message: Optional[str] = None  # 실패 시 에러 메시지


@dataclass
class ToolStatRecord:
    """도구 효율표 레코드."""
    tool_name: str  # 사용된 도구 명칭
    use_count: int  # 총 사용 횟수
    success_count: int  # 성공 횟수
    last_error_msg: Optional[str] = None  # 가장 최근 발생한 에러 메시지
    avg_runtime_ms: float = 0.0  # 평균 실행 지연 시간


@dataclass
class GoalRecord:
    """목표 트리 레코드."""
    id: str  # UUID 기반 고유 식별자
    title: str  # 목표의 핵심 요약
    description: str  # 상세 수행 내용
    parent_id: Optional[str] = None  # 부모 목표 ID (Root는 None)
    priority: int = 0  # 실행 우선순위
    status: str = "TO_DO"  # TO_DO, IN_PROGRESS, DONE, FAILED
    depth: int = 0  # 트리 깊이
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class BehaviorInsight:
    """행동 로그 분석 통찰 레코드."""
    id: str  # UUID 기반 고유 식별자
    pattern_type: str  # 패턴 유형 (예: "failure_pattern", "tool_performance")
    finding: str  # 발견된 패턴/문제점
    recommendation: str  # 구체적인 개선 권고
    confidence: float = 0.5  # 신뢰도 (0.0 ~ 1.0)
    is_applied: int = 0  # 적용 여부 (0: 미적용, 1: 적용됨)
    is_verified_by_guardian: int = 0  # 보호자 2차 검수 승인 여부 (0: 미승인, 1: 승인)
    created_at: Optional[datetime] = None


@dataclass
class ScheduledTask:
    """예약된 태스크 레코드."""
    id: str  # UUID 기반 고유 식별자
    task_name: str  # 작업 명칭
    task_type: str  # 실행할 작업 유형 (AgentBrain 연동용)
    schedule_expr: str  # 실행 주기 (Cron 표현식 또는 interval_seconds)
    args_json: str  # 작업 실행 시 필요한 매개변수 (JSON 문자열)
    next_run_at: datetime  # 다음 실행 예정 시각
    status: str = "ENABLED"  # ENABLED, DISABLED, RUNNING
    last_run_at: Optional[datetime] = None  # 마지막 실행 시각
    consecutive_failures: int = 0  # 연속 실패 횟수
    root_goal_id: Optional[str] = None  # 연결된 목표 트리 루트 ID
    created_at: Optional[datetime] = None


@dataclass
class DynamicToolRecord:
    """동적 도구 레코드 (Phase 4: 동적 도구 확장)."""
    id: str  # UUID
    tool_name: str
    description: str
    code: str
    parameters_json: str  # JSON 문자열
    author_agent_id: Optional[str] = None
    status: str = "PENDING"  # PENDING, VERIFIED, REJECTED
    created_at: Optional[datetime] = None


@dataclass
class AutonomousWorkResult:
    """자율 작업 결과 레코드 (승인 대기/윤리 검토/실행 완료)."""
    id: str
    task_type: str
    tools_created: Optional[str] = None
    info_collected: Optional[str] = None
    ethics_review: Optional[str] = None
    ethics_approved: int = 0
    status: str = "PENDING"  # PENDING, ETHICS_PASS, ETHICS_FAIL, WAITING_FOR_APPROVAL, APPROVED, REJECTED, QUARANTINED, COMPLETED
    output: Optional[str] = None  # 실행 결과 (stdout/stderr)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class EvolutionLogRecord:
    """진화(자기 수정) 제안서 레코드 (Phase 5)."""
    id: str
    target_file: str
    proposed_code: str
    reason: str
    diff_preview: Optional[str] = None
    status: str = "DRAFT"  # DRAFT, DRY_RUN, TESTS_PENDING, APPROVAL_PENDING, APPLIED, REJECTED, ROLLED_BACK
    previous_content: Optional[str] = None
    author_agent_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None  # 적용 시각 (피드백 루프용)
    feedback_failed: int = 0  # 1이면 적용 후 에러율 급증 → FAILED 취급, 롤백 권고
    root_goal_id: Optional[str] = None  # 목표 주도 진화: 연결된 목표(goal) ID


# =============================================================================
# 데이터베이스 인터페이스
# =============================================================================

class MemoryDatabase:
    """
    영속적 경험 메모리 데이터베이스 인터페이스.
    
    SQLite를 사용하여 experience_ledger와 tool_stats 테이블을 관리합니다.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Args:
            db_path: 데이터베이스 파일 경로 (None이면 기본 경로 사용)
        """
        if db_path is None:
            # 기본 경로: mellow_link/data/mellow_link_memory.db
            base_dir = Path(__file__).parent.parent
            data_dir = base_dir / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "mellow_link_memory.db"
        
        self.db_path = db_path
        self._init_database()

    @contextmanager
    def _get_connection(self):
        """
        SQLite 커넥션 생성 및 쓰기 성능 최적화 PRAGMA 적용.
        모든 쿼리에서 동일한 최적화가 적용되도록 사용.
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON;")  # 외래 키 제약 강제
            conn.execute("PRAGMA busy_timeout = 5000;")  # 쓰기 경합 시 5초 대기
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA cache_size=-64000;")
            yield conn
        finally:
            conn.close()

    def _init_database(self) -> None:
        """데이터베이스 테이블 초기화."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # WAL 모드는 DB 수준에서 한 번 설정 (쓰기 성능 개선)
            cursor.execute("PRAGMA journal_mode=WAL;")
            conn.commit()
            
            # experience_ledger 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS experience_ledger (
                    id TEXT PRIMARY KEY,
                    task_intent TEXT NOT NULL,
                    task_hash TEXT NOT NULL,
                    context_summary TEXT NOT NULL,
                    action_steps TEXT NOT NULL,
                    final_outcome TEXT NOT NULL,
                    is_success INTEGER NOT NULL CHECK(is_success IN (0, 1)),
                    critique_tag TEXT,
                    lessons_learned TEXT,
                    embedding BLOB,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # FTS5 가상 테이블 생성 (검색 성능 최적화: 10-50배 향상)
            # NOTE: id는 TEXT(UUID)이므로 content_rowid 대신 독립 FTS 사용
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
                    record_id,
                    task_intent,
                    context_summary
                )
            """)

            # FTS 인덱스 자동 동기화 트리거 (TEXT id 지원)
            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS experience_ai AFTER INSERT ON experience_ledger BEGIN
                    INSERT INTO experience_fts(record_id, task_intent, context_summary)
                    VALUES (new.id, new.task_intent, new.context_summary);
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS experience_ad AFTER DELETE ON experience_ledger BEGIN
                    DELETE FROM experience_fts WHERE record_id = old.id;
                END
            """)

            cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS experience_au AFTER UPDATE ON experience_ledger BEGIN
                    UPDATE experience_fts
                    SET task_intent = new.task_intent, context_summary = new.context_summary
                    WHERE record_id = new.id;
                END
            """)

            # 기존 데이터를 FTS 인덱스에 동기화 (마이그레이션)
            try:
                cursor.execute("""
                    INSERT INTO experience_fts(record_id, task_intent, context_summary)
                    SELECT id, task_intent, context_summary FROM experience_ledger
                    WHERE id NOT IN (SELECT record_id FROM experience_fts)
                """)
                logger.info("[MemoryDatabase] FTS index migration completed")
            except sqlite3.OperationalError as e:
                # FTS 테이블이 이미 존재하거나 다른 오류인 경우 무시
                logger.debug(f"[MemoryDatabase] FTS migration skipped: {e}")
            
            # task_hash 인덱스 (이미 존재할 수 있음)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_experience_task_hash ON experience_ledger(task_hash)
            """)
            
            # is_success 인덱스 (성공/실패 필터링 최적화)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_experience_success ON experience_ledger(is_success, created_at DESC)
            """)
            
            # tool_stats 테이블 생성
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_stats (
                    tool_name TEXT PRIMARY KEY,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    success_count INTEGER NOT NULL DEFAULT 0,
                    last_error_msg TEXT,
                    avg_runtime_ms REAL NOT NULL DEFAULT 0.0
                )
            """)
            
            # session_checkpoints 테이블 생성 (세션 상태 복원용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_checkpoints (
                    session_id TEXT PRIMARY KEY,
                    task_intent TEXT NOT NULL,
                    current_step INTEGER NOT NULL DEFAULT 0,
                    history_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('RUNNING', 'PAUSED', 'COMPLETED')),
                    pause_reason TEXT,
                    original_max_turns INTEGER,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # goals 테이블 생성 (목표 트리 시스템용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY,
                    parent_id TEXT,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    priority INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK(status IN ('TO_DO', 'IN_PROGRESS', 'DONE', 'FAILED')) DEFAULT 'TO_DO',
                    depth INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (parent_id) REFERENCES goals(id) ON DELETE CASCADE
                )
            """)
            
            # behavior_insights 테이블 생성 (행동 로그 분석 결과 저장용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS behavior_insights (
                    id TEXT PRIMARY KEY,
                    pattern_type TEXT NOT NULL,
                    finding TEXT NOT NULL,
                    recommendation TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
                    is_applied INTEGER NOT NULL DEFAULT 0 CHECK(is_applied IN (0, 1)),
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # scheduled_tasks 테이블 생성 (자율 태스크 스케줄러용)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    task_name TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    schedule_expr TEXT NOT NULL,
                    args_json TEXT NOT NULL,
                    next_run_at DATETIME NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('ENABLED', 'DISABLED', 'RUNNING')) DEFAULT 'ENABLED',
                    last_run_at DATETIME,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    root_goal_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 기존 테이블에 컬럼 추가 (마이그레이션)
            try:
                cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN consecutive_failures INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # 컬럼이 이미 존재하면 무시
            
            try:
                cursor.execute("ALTER TABLE scheduled_tasks ADD COLUMN root_goal_id TEXT")
            except sqlite3.OperationalError:
                pass  # 컬럼이 이미 존재하면 무시
            
            # performance_metrics 테이블 생성 (성능 자가 진단용)
            # Phase 1: INFER_MS, TPS_APPROX, TTFT_MEASURED (non-stream baseline)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    metric_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL CHECK(category IN (
                        'TOOL', 'LATENCY', 'TOKEN', 'GOAL', 'TASK_SUCCESS', 'CRITICAL_ERROR',
                        'VERIFY_COVERAGE', 'ERROR_RECURRENCE',
                        'TTFT_MS', 'TPS', 'TPS_APPROX', 'TOKENS_IN', 'TOKENS_OUT', 'OBSERVATION_VIOLATION',
                        'INFER_MS', 'TTFT_MEASURED'
                    )),
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 인덱스 생성 (카테고리 및 타임스탬프 기반 조회 최적화)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_category_timestamp 
                ON performance_metrics(category, timestamp DESC)
            """)
            
            # dynamic_tools 테이블 (Phase 4: 동적 도구 확장)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS dynamic_tools (
                    id TEXT PRIMARY KEY,
                    tool_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    code TEXT NOT NULL,
                    parameters_json TEXT NOT NULL DEFAULT '{}',
                    author_agent_id TEXT,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'VERIFIED', 'REJECTED')) DEFAULT 'PENDING',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_dynamic_tools_status ON dynamic_tools(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_dynamic_tools_name ON dynamic_tools(tool_name)
            """)
            
            # evolution_logs 테이블 (Phase 5: 자기 수정 제안서)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS evolution_logs (
                    id TEXT PRIMARY KEY,
                    target_file TEXT NOT NULL,
                    proposed_code TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    diff_preview TEXT,
                    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'DRY_RUN', 'TESTS_PENDING', 'APPROVAL_PENDING', 'APPLIED', 'REJECTED', 'ROLLED_BACK')) DEFAULT 'DRAFT',
                    previous_content TEXT,
                    author_agent_id TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_evolution_status ON evolution_logs(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_evolution_target ON evolution_logs(target_file)
            """)
            
            try:
                cursor.execute("ALTER TABLE session_checkpoints ADD COLUMN pause_reason TEXT")
            except sqlite3.OperationalError:
                pass  # 컬럼이 이미 존재하면 무시
            
            try:
                cursor.execute("ALTER TABLE session_checkpoints ADD COLUMN original_max_turns INTEGER")
            except sqlite3.OperationalError:
                pass  # 컬럼이 이미 존재하면 무시

            # experience_ledger 확장 (경험 장부 훅: latency_ms, used_tools, error_message)
            for col, typ in [
                ("latency_ms", "REAL"),
                ("used_tools", "TEXT"),
                ("error_message", "TEXT"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE experience_ledger ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass
            
            try:
                cursor.execute(
                    "ALTER TABLE behavior_insights ADD COLUMN is_verified_by_guardian INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # 컬럼이 이미 존재하면 무시

            # evolution_logs 피드백 루프·목표 연동용 컬럼
            for col, typ in [
                ("applied_at", "DATETIME"),
                ("feedback_failed", "INTEGER DEFAULT 0"),
                ("root_goal_id", "TEXT"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE evolution_logs ADD COLUMN {col} {typ}")
                except sqlite3.OperationalError:
                    pass

            # autonomous_work_results 테이블 (자율 작업 결과 및 승인 대기)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS autonomous_work_results (
                    id TEXT PRIMARY KEY,
                    task_type TEXT NOT NULL,
                    tools_created TEXT,
                    info_collected TEXT,
                    ethics_review TEXT,
                    ethics_approved INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL CHECK(status IN ('PENDING', 'ETHICS_PASS', 'ETHICS_FAIL', 'WAITING_FOR_APPROVAL', 'APPROVED', 'REJECTED', 'QUARANTINED', 'EXECUTING', 'COMPLETED')) DEFAULT 'PENDING',
                    output TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_autonomous_status ON autonomous_work_results(status)
            """)
            # output 컬럼 마이그레이션 (기존 DB)
            try:
                cursor.execute("ALTER TABLE autonomous_work_results ADD COLUMN output TEXT")
            except sqlite3.OperationalError:
                pass  # 이미 존재하면 무시

            # EXECUTING 상태 마이그레이션 (기존 DB CHECK 제약에 EXECUTING 추가)
            try:
                cursor.execute("INSERT INTO autonomous_work_results (id, task_type, ethics_approved, status, created_at, updated_at) VALUES ('__executing_migration_probe__', 'probe', 0, 'EXECUTING', datetime('now'), datetime('now'))")
                cursor.execute("DELETE FROM autonomous_work_results WHERE id='__executing_migration_probe__'")
            except sqlite3.IntegrityError:
                conn.rollback()
                cursor.execute("""
                    CREATE TABLE autonomous_work_results_new (
                        id TEXT PRIMARY KEY,
                        task_type TEXT NOT NULL,
                        tools_created TEXT,
                        info_collected TEXT,
                        ethics_review TEXT,
                        ethics_approved INTEGER NOT NULL DEFAULT 0,
                        status TEXT NOT NULL CHECK(status IN ('PENDING', 'ETHICS_PASS', 'ETHICS_FAIL', 'WAITING_FOR_APPROVAL', 'APPROVED', 'REJECTED', 'QUARANTINED', 'EXECUTING', 'COMPLETED')) DEFAULT 'PENDING',
                        output TEXT,
                        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    INSERT INTO autonomous_work_results_new
                    SELECT id, task_type, tools_created, info_collected, ethics_review, ethics_approved, status, output, created_at, updated_at
                    FROM autonomous_work_results
                """)
                cursor.execute("DROP TABLE autonomous_work_results")
                cursor.execute("ALTER TABLE autonomous_work_results_new RENAME TO autonomous_work_results")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_autonomous_status ON autonomous_work_results(status)")
            except sqlite3.OperationalError:
                pass  # 테이블 없음 등

            # api_usage_logs 테이블 (Guardian API 비용 추적)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_usage_logs (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    cost REAL NOT NULL DEFAULT 0.0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_usage_provider_date
                ON api_usage_logs(provider, created_at)
            """)
            
            # 인덱스 생성 (검색 성능 향상)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_hash ON experience_ledger(task_hash)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_is_success ON experience_ledger(is_success)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON experience_ledger(created_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_status ON session_checkpoints(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_updated ON session_checkpoints(updated_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_goals_priority ON goals(priority DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_insights_pattern ON behavior_insights(pattern_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_insights_confidence ON behavior_insights(confidence DESC)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_insights_applied ON behavior_insights(is_applied)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_insights_verified ON behavior_insights(is_verified_by_guardian)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_scheduled_next_run ON scheduled_tasks(next_run_at)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_scheduled_status ON scheduled_tasks(status)
            """)
            
            conn.commit()
            logger.info(f"[MemoryDatabase] Database initialized at {self.db_path}")

    def save_experience(self, record: ExperienceRecord) -> bool:
        """
        경험 레코드를 데이터베이스에 저장.
        
        Args:
            record: 저장할 경험 레코드
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # created_at이 없으면 현재 시간 사용
                created_at = record.created_at or datetime.now()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO experience_ledger (
                        id, task_intent, task_hash, context_summary,
                        action_steps, final_outcome, is_success,
                        critique_tag, lessons_learned, embedding, created_at,
                        latency_ms, used_tools, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.id,
                    record.task_intent,
                    record.task_hash,
                    record.context_summary,
                    record.action_steps,
                    record.final_outcome,
                    record.is_success,
                    record.critique_tag,
                    record.lessons_learned,
                    record.embedding,
                    created_at.isoformat(),
                    getattr(record, "latency_ms", None),
                    getattr(record, "used_tools", None),
                    getattr(record, "error_message", None),
                ))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Experience saved: {record.id}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save experience: {e}")
            return False

    def record_ledger_entry(
        self,
        timestamp: datetime,
        intent_type: str,
        is_success: int,
        latency_ms: float,
        used_tools: List[str],
        error_message: Optional[str] = None,
    ) -> bool:
        """
        ✅ verified: 경험 장부 훅 — 요청별 최소 항목만 기록 (비동기 호출용).
        timestamp, intent_type, is_success, latency_ms, used_tools, error_message.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                record_id = str(uuid.uuid4())
                task_hash = MemoryDatabase.compute_task_hash(intent_type, "")
                used_tools_json = json.dumps(used_tools or [], ensure_ascii=False)
                created_at = timestamp.isoformat() if isinstance(timestamp, datetime) else timestamp
                cursor.execute("""
                    INSERT INTO experience_ledger (
                        id, task_intent, task_hash, context_summary,
                        action_steps, final_outcome, is_success,
                        critique_tag, lessons_learned, embedding, created_at,
                        latency_ms, used_tools, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record_id,
                    (intent_type or "chat")[:2000],
                    task_hash,
                    "",
                    used_tools_json,
                    (error_message or "")[:5000],
                    1 if is_success else 0,
                    None,
                    None,
                    None,
                    created_at,
                    latency_ms,
                    used_tools_json,
                    (error_message or "")[:2000] if error_message else None,
                ))
                conn.commit()
                logger.debug(f"[MemoryDatabase] Ledger entry recorded: {record_id}")
                return True
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to record ledger entry: {e}")
            return False

    def get_experience(self, experience_id: str) -> Optional[ExperienceRecord]:
        """
        ID로 경험 레코드 조회.
        
        Args:
            experience_id: 경험 ID
            
        Returns:
            ExperienceRecord 또는 None
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM experience_ledger WHERE id = ?
                """, (experience_id,))
                
                row = cursor.fetchone()
                if row:
                    return self._row_to_experience(row)
                return None
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get experience: {e}")
            return None

    def search_similar_experiences(
        self,
        task_hash: str,
        limit: int = 5
    ) -> List[ExperienceRecord]:
        """
        유사한 작업 경험 검색 (task_hash 기반).
        
        Args:
            task_hash: 검색할 작업 해시
            limit: 최대 반환 개수
            
        Returns:
            유사한 경험 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM experience_ledger
                    WHERE task_hash = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (task_hash, limit))
                
                rows = cursor.fetchall()
                return [self._row_to_experience(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to search experiences: {e}")
            return []

    def get_relevant_experiences(
        self,
        task_intent: str,
        task_hash: Optional[str] = None,
        limit: int = 3
    ) -> List[ExperienceRecord]:
        """
        관련 경험 검색 (task_hash 일치 또는 task_intent 키워드 포함).
        
        성공 사례(is_success=1)를 우선적으로 반환합니다.
        
        Args:
            task_intent: 작업 의도 (키워드 검색용)
            task_hash: 작업 해시 (정확 일치 검색용, 선택사항)
            limit: 최대 반환 개수
            
        Returns:
            관련 경험 레코드 리스트 (성공 사례 우선)
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # task_intent에서 키워드 추출 (간단한 토큰화)
                keywords = self._extract_keywords(task_intent)
                
                # 쿼리 구성: task_hash 일치 또는 키워드 포함
                conditions = []
                params = []
                
                if task_hash:
                    conditions.append("task_hash = ?")
                    params.append(task_hash)
                
                if keywords:
                    # FTS5를 사용한 빠른 검색 (O(log N) vs O(N) LIKE)
                    # 키워드를 공백으로 연결하여 FTS 쿼리 생성
                    fts_query = " OR ".join(keywords)
                    # FTS 검색 결과와 조인하여 원본 테이블 조회 (record_id 사용)
                    conditions.append("""
                        id IN (
                            SELECT record_id FROM experience_fts
                            WHERE experience_fts MATCH ?
                        )
                    """)
                    params.append(fts_query)
                
                if not conditions:
                    # 조건이 없으면 최근 성공 사례 반환
                    cursor.execute("""
                        SELECT * FROM experience_ledger
                        WHERE is_success = 1
                        ORDER BY created_at DESC
                        LIMIT ?
                    """, (limit,))
                else:
                    # 성공 사례 우선 정렬
                    where_clause = " OR ".join(conditions)
                    cursor.execute(f"""
                        SELECT * FROM experience_ledger
                        WHERE {where_clause}
                        ORDER BY is_success DESC, created_at DESC
                        LIMIT ?
                    """, params + [limit])
                
                rows = cursor.fetchall()
                experiences = [self._row_to_experience(row) for row in rows]
                
                # 성공 사례를 앞으로 정렬 (SQL ORDER BY가 완벽하지 않을 수 있으므로)
                experiences.sort(key=lambda x: (x.is_success == 0, x.created_at or datetime.min), reverse=True)
                
                return experiences[:limit]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get relevant experiences: {e}")
            return []

    def _extract_keywords(self, text: str, min_length: int = 2) -> List[str]:
        """
        텍스트에서 키워드 추출 (간단한 토큰화).
        
        Args:
            text: 텍스트
            min_length: 최소 키워드 길이
            
        Returns:
            키워드 리스트
        """
        # 간단한 토큰화: 공백/구두점으로 분리
        # 한글, 영문, 숫자만 추출
        tokens = re.findall(r'[\w가-힣]+', text.lower())
        # 최소 길이 이상이고 너무 짧지 않은 토큰만 반환
        keywords = [t for t in tokens if len(t) >= min_length and len(t) <= 20]
        # 중복 제거 및 빈도가 높은 순으로 정렬 (간단히 길이로 필터링)
        return list(set(keywords))[:10]  # 최대 10개 키워드

    def update_tool_stat(
        self,
        tool_name: str,
        is_success: bool,
        runtime_ms: float,
        error_msg: Optional[str] = None
    ) -> bool:
        """
        도구 통계 업데이트.
        
        Args:
            tool_name: 도구 이름
            is_success: 성공 여부
            runtime_ms: 실행 시간 (밀리초)
            error_msg: 에러 메시지 (실패 시)
            
        Returns:
            업데이트 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 기존 통계 조회
                cursor.execute("""
                    SELECT use_count, success_count, avg_runtime_ms
                    FROM tool_stats WHERE tool_name = ?
                """, (tool_name,))
                
                row = cursor.fetchone()
                
                if row:
                    # 기존 통계 업데이트
                    old_use_count, old_success_count, old_avg_runtime = row
                    new_use_count = old_use_count + 1
                    new_success_count = old_success_count + (1 if is_success else 0)
                    
                    # 평균 실행 시간 계산 (가중 평균)
                    total_runtime = (old_avg_runtime * old_use_count) + runtime_ms
                    new_avg_runtime = total_runtime / new_use_count
                    
                    cursor.execute("""
                        UPDATE tool_stats SET
                            use_count = ?,
                            success_count = ?,
                            avg_runtime_ms = ?,
                            last_error_msg = ?
                        WHERE tool_name = ?
                    """, (
                        new_use_count,
                        new_success_count,
                        new_avg_runtime,
                        error_msg if not is_success else None,
                        tool_name
                    ))
                else:
                    # 새 통계 생성
                    cursor.execute("""
                        INSERT INTO tool_stats (
                            tool_name, use_count, success_count,
                            last_error_msg, avg_runtime_ms
                        ) VALUES (?, ?, ?, ?, ?)
                    """, (
                        tool_name,
                        1,
                        1 if is_success else 0,
                        error_msg if not is_success else None,
                        runtime_ms
                    ))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Tool stat updated: {tool_name}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to update tool stat: {e}")
            return False

    def get_tool_stat(self, tool_name: str) -> Optional[ToolStatRecord]:
        """
        도구 통계 조회.
        
        Args:
            tool_name: 도구 이름
            
        Returns:
            ToolStatRecord 또는 None
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM tool_stats WHERE tool_name = ?
                """, (tool_name,))
                
                row = cursor.fetchone()
                if row:
                    return ToolStatRecord(
                        tool_name=row["tool_name"],
                        use_count=row["use_count"],
                        success_count=row["success_count"],
                        last_error_msg=row["last_error_msg"],
                        avg_runtime_ms=row["avg_runtime_ms"]
                    )
                return None
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get tool stat: {e}")
            return None

    def get_all_tool_stats(self) -> List[ToolStatRecord]:
        """
        모든 도구 통계 조회.
        
        Returns:
            ToolStatRecord 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM tool_stats
                    ORDER BY use_count DESC
                """)
                
                rows = cursor.fetchall()
                return [
                    ToolStatRecord(
                        tool_name=row["tool_name"],
                        use_count=row["use_count"],
                        success_count=row["success_count"],
                        last_error_msg=row["last_error_msg"],
                        avg_runtime_ms=row["avg_runtime_ms"]
                    )
                    for row in rows
                ]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get all tool stats: {e}")
            return []

    def _row_to_experience(self, row: sqlite3.Row) -> ExperienceRecord:
        """SQLite Row를 ExperienceRecord로 변환."""
        created_at = None
        if row["created_at"]:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except ValueError:
                pass
        
        return ExperienceRecord(
            id=row["id"],
            task_intent=row["task_intent"],
            task_hash=row["task_hash"],
            context_summary=row["context_summary"],
            action_steps=row["action_steps"],
            final_outcome=row["final_outcome"],
            is_success=row["is_success"],
            critique_tag=row["critique_tag"],
            lessons_learned=row["lessons_learned"],
            embedding=row["embedding"],
            created_at=created_at,
            latency_ms=row["latency_ms"] if "latency_ms" in row.keys() else None,
            used_tools=row["used_tools"] if "used_tools" in row.keys() else None,
            error_message=row["error_message"] if "error_message" in row.keys() else None,
        )

    def save_checkpoint(
        self,
        session_id: str,
        task_intent: str,
        current_step: int,
        history_json: str,
        status: str = "RUNNING",
        pause_reason: Optional[str] = None,
        original_max_turns: Optional[int] = None
    ) -> bool:
        """
        세션 체크포인트 저장.
        
        Args:
            session_id: 세션 ID
            task_intent: 작업 의도
            current_step: 현재 스텝 번호
            history_json: 히스토리 JSON 문자열
            status: 상태 (RUNNING, PAUSED, COMPLETED)
            pause_reason: 일시 중지 사유 (PAUSED 상태일 때)
            original_max_turns: 원본 최대 턴 수
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    INSERT OR REPLACE INTO session_checkpoints (
                        session_id, task_intent, current_step,
                        history_json, status, pause_reason,
                        original_max_turns, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    task_intent,
                    current_step,
                    history_json,
                    status,
                    pause_reason,
                    original_max_turns,
                    datetime.now().isoformat(),
                ))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Checkpoint saved: {session_id} (step {current_step})")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save checkpoint: {e}")
            return False

    def load_checkpoint(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        세션 체크포인트 로드.
        
        Args:
            session_id: 세션 ID
            
        Returns:
            체크포인트 데이터 딕셔너리 또는 None
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM session_checkpoints
                    WHERE session_id = ? AND status IN ('RUNNING', 'PAUSED')
                """, (session_id,))
                
                row = cursor.fetchone()
                if row:
                    result = {
                        "session_id": row["session_id"],
                        "task_intent": row["task_intent"],
                        "current_step": row["current_step"],
                        "history_json": row["history_json"],
                        "status": row["status"],
                        "updated_at": row["updated_at"],
                    }
                    # 새 컬럼 추가 (없을 수 있음)
                    if "pause_reason" in row.keys():
                        result["pause_reason"] = row["pause_reason"]
                    if "original_max_turns" in row.keys():
                        result["original_max_turns"] = row["original_max_turns"]
                    return result
                return None
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to load checkpoint: {e}")
            return None

    def clear_checkpoint(self, session_id: str, mark_completed: bool = True) -> bool:
        """
        세션 체크포인트 삭제 또는 완료 표시.
        
        Args:
            session_id: 세션 ID
            mark_completed: True면 COMPLETED로 변경, False면 삭제
            
        Returns:
            성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                if mark_completed:
                    cursor.execute("""
                        UPDATE session_checkpoints
                        SET status = 'COMPLETED', updated_at = ?
                        WHERE session_id = ?
                    """, (datetime.now().isoformat(), session_id))
                else:
                    cursor.execute("""
                        DELETE FROM session_checkpoints
                        WHERE session_id = ?
                    """, (session_id,))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Checkpoint cleared: {session_id}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to clear checkpoint: {e}")
            return False

    def save_goal(self, goal: GoalRecord) -> bool:
        """
        목표 레코드를 데이터베이스에 저장.
        
        Args:
            goal: 저장할 목표 레코드
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                created_at = goal.created_at or datetime.now()
                updated_at = goal.updated_at or datetime.now()
                
                cursor.execute("""
                    INSERT INTO goals (
                        id, parent_id, title, description,
                        priority, status, depth, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        parent_id = excluded.parent_id,
                        title = excluded.title,
                        description = excluded.description,
                        priority = excluded.priority,
                        status = excluded.status,
                        depth = excluded.depth,
                        updated_at = excluded.updated_at
                """, (
                    goal.id,
                    goal.parent_id,
                    goal.title,
                    goal.description,
                    goal.priority,
                    goal.status,
                    goal.depth,
                    created_at.isoformat(),
                    updated_at.isoformat(),
                ))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Goal saved: {goal.id}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save goal: {e}")
            return False

    def get_goal(self, goal_id: str) -> Optional[GoalRecord]:
        """
        ID로 목표 레코드 조회.
        
        Args:
            goal_id: 목표 ID
            
        Returns:
            GoalRecord 또는 None
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM goals WHERE id = ?
                """, (goal_id,))
                
                row = cursor.fetchone()
                if row:
                    return self._row_to_goal(row)
                return None
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get goal: {e}")
            return None

    def get_children_goals(self, parent_id: str) -> List[GoalRecord]:
        """
        부모 목표의 모든 자식 목표 조회.
        
        Args:
            parent_id: 부모 목표 ID
            
        Returns:
            자식 목표 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM goals
                    WHERE parent_id = ?
                    ORDER BY priority DESC, created_at ASC
                """, (parent_id,))
                
                rows = cursor.fetchall()
                return [self._row_to_goal(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get children goals: {e}")
            return []

    def get_root_goals(self) -> List[GoalRecord]:
        """
        모든 루트 목표 조회 (parent_id가 NULL인 목표).
        
        Returns:
            루트 목표 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM goals
                    WHERE parent_id IS NULL
                    ORDER BY priority DESC, created_at ASC
                """)
                
                rows = cursor.fetchall()
                return [self._row_to_goal(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get root goals: {e}")
            return []

    def update_goal_status(self, goal_id: str, status: str) -> bool:
        """
        목표 상태 업데이트.
        
        Args:
            goal_id: 목표 ID
            status: 새 상태 (TO_DO, IN_PROGRESS, DONE, FAILED)
            
        Returns:
            업데이트 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                cursor.execute("""
                    UPDATE goals
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                """, (status, datetime.now().isoformat(), goal_id))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Goal status updated: {goal_id} -> {status}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to update goal status: {e}")
            return False

    def get_active_goals(self, limit: int = 20) -> List[GoalRecord]:
        """
        ✅ verified: 미완료 활성 목표 조회 (Goal-Trigger 연동용).
        status IN ('TO_DO', 'IN_PROGRESS'), 우선순위·생성순.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM goals
                    WHERE status IN ('TO_DO', 'IN_PROGRESS')
                    ORDER BY priority DESC, created_at ASC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                return [self._row_to_goal(row) for row in rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get active goals: {e}")
            return []

    def get_executable_goals(self, limit: int = 1) -> List[GoalRecord]:
        """
        실행 가능한 목표 조회 (TO_DO 상태의 리프 목표만, 우선순위 높은 순).
        
        리프 목표(Leaf Goal): 자식 목표가 없는 목표만 반환합니다.
        복합 목표(Composite Goal)는 직접 실행되지 않고 자식들의 상태를 요약하는 역할만 합니다.
        
        Args:
            limit: 최대 반환 개수
            
        Returns:
            실행 가능한 리프 목표 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # 리프 목표만 선택: 자식이 없는 목표 (LEFT JOIN으로 자식 존재 여부 확인)
                cursor.execute("""
                    SELECT g.* FROM goals g
                    LEFT JOIN goals children ON children.parent_id = g.id
                    WHERE g.status = 'TO_DO'
                      AND children.id IS NULL
                    ORDER BY g.priority DESC, g.depth ASC, g.created_at ASC
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                return [self._row_to_goal(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get executable goals: {e}")
            return []

    def get_all_goals_by_status(self, status: Optional[str] = None) -> List[GoalRecord]:
        """
        특정 상태의 모든 목표 조회 (단일 SQL 쿼리로 최적화).
        
        status가 None이면 WHERE 절을 생략하여 전체 목표를 조회합니다.
        (SQLite에서 status = NULL은 항상 False를 반환하므로 분기 필요)
        
        Args:
            status: 목표 상태 (None이면 전체 조회)
            
        Returns:
            목표 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if status is not None:
                    cursor.execute("""
                        SELECT * FROM goals
                        WHERE status = ?
                        ORDER BY priority DESC, depth ASC, created_at ASC
                    """, (status,))
                else:
                    cursor.execute("""
                        SELECT * FROM goals
                        ORDER BY priority DESC, depth ASC, created_at ASC
                    """)
                
                rows = cursor.fetchall()
                return [self._row_to_goal(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get goals by status: {e}")
            return []

    def _row_to_goal(self, row: sqlite3.Row) -> GoalRecord:
        """SQLite Row를 GoalRecord로 변환."""
        created_at = None
        updated_at = None
        
        if row["created_at"]:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except ValueError:
                pass
        
        if row["updated_at"]:
            try:
                updated_at = datetime.fromisoformat(row["updated_at"])
            except ValueError:
                pass
        
        return GoalRecord(
            id=row["id"],
            parent_id=row["parent_id"],
            title=row["title"],
            description=row["description"],
            priority=row["priority"],
            status=row["status"],
            depth=row["depth"],
            created_at=created_at,
            updated_at=updated_at
        )

    def save_insight(self, insight: BehaviorInsight) -> bool:
        """
        행동 로그 분석 통찰을 데이터베이스에 저장 (중복 방지).
        
        유사한 finding이 있으면 기존 레코드를 갱신(Update)만 수행합니다.
        
        Args:
            insight: 저장할 통찰 레코드
            
        Returns:
            저장 성공 여부
        """
        try:
            # 타입 검증: dict/list가 전달되면 JSON 문자열로 변환
            def _ensure_str(val: Any) -> str:
                if val is None:
                    return ""
                if isinstance(val, (dict, list)):
                    import json
                    return json.dumps(val, ensure_ascii=False)
                return str(val)

            pattern_type = _ensure_str(insight.pattern_type)
            finding = _ensure_str(insight.finding)
            recommendation = _ensure_str(insight.recommendation)

            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 유사한 finding이 있는지 확인 (간단한 문자열 유사도)
                cursor.execute("""
                    SELECT id FROM behavior_insights
                    WHERE pattern_type = ?
                      AND finding LIKE ?
                    LIMIT 1
                """, (
                    pattern_type,
                    f"%{finding[:50]}%"  # finding의 처음 50자로 유사도 판단
                ))
                
                existing_row = cursor.fetchone()
                
                if existing_row:
                    # 기존 레코드 갱신
                    existing_id = existing_row[0]
                    verified = getattr(insight, "is_verified_by_guardian", 0)
                    confidence = float(insight.confidence) if insight.confidence is not None else 0.5
                    cursor.execute("""
                        UPDATE behavior_insights SET
                            finding = ?,
                            recommendation = ?,
                            confidence = ?,
                            is_applied = ?,
                            is_verified_by_guardian = ?,
                            created_at = ?
                        WHERE id = ?
                    """, (
                        finding,
                        recommendation,
                        confidence,
                        int(insight.is_applied or 0),
                        int(verified or 0),
                        (insight.created_at or datetime.now()).isoformat(),
                        existing_id
                    ))
                    logger.debug(f"[MemoryDatabase] Insight updated (deduplication): {existing_id}")
                else:
                    # 새 레코드 삽입
                    created_at = insight.created_at or datetime.now()
                    verified = getattr(insight, "is_verified_by_guardian", 0)
                    confidence = float(insight.confidence) if insight.confidence is not None else 0.5
                    cursor.execute("""
                        INSERT INTO behavior_insights (
                            id, pattern_type, finding, recommendation,
                            confidence, is_applied, is_verified_by_guardian, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        _ensure_str(insight.id),
                        pattern_type,
                        finding,
                        recommendation,
                        confidence,
                        int(insight.is_applied or 0),
                        int(verified or 0),
                        created_at.isoformat(),
                    ))
                    logger.debug(f"[MemoryDatabase] Insight saved: {insight.id}")
                
                conn.commit()
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save insight: {e}")
            return False

    def get_recent_insights(
        self,
        limit: int = 10,
        min_confidence: float = 0.0,
        pattern_type: Optional[str] = None,
        days_threshold: int = 7,
        prefer_verified: bool = True
    ) -> List[BehaviorInsight]:
        """
        최근 통찰 조회 (시간 필터 적용).
        
        Recency Bias 방지 및 Drift 해결을 위해 지정된 일수 이내의 통찰만 반환합니다.
        prefer_verified=True이면 보호자 2차 검수 승인 통찰을 우선 정렬합니다.
        
        Args:
            limit: 최대 반환 개수
            min_confidence: 최소 신뢰도
            pattern_type: 패턴 유형 필터 (None이면 모든 유형)
            days_threshold: 최근 N일 이내의 통찰만 조회 (기본 7일)
            prefer_verified: True면 is_verified_by_guardian=1 통찰 우선
            
        Returns:
            통찰 레코드 리스트 (승인 우선, 신뢰도 높은 순)
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cutoff_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()
                order_clause = (
                    "ORDER BY is_verified_by_guardian DESC, confidence DESC, created_at DESC"
                    if prefer_verified else "ORDER BY confidence DESC, created_at DESC"
                )
                
                if pattern_type:
                    cursor.execute(f"""
                        SELECT * FROM behavior_insights
                        WHERE confidence >= ? 
                          AND pattern_type = ?
                          AND created_at >= ?
                        {order_clause}
                        LIMIT ?
                    """, (min_confidence, pattern_type, cutoff_date, limit))
                else:
                    cursor.execute(f"""
                        SELECT * FROM behavior_insights
                        WHERE confidence >= ?
                          AND created_at >= ?
                        {order_clause}
                        LIMIT ?
                    """, (min_confidence, cutoff_date, limit))
                
                rows = cursor.fetchall()
                return [self._row_to_insight(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get recent insights: {e}")
            return []

    def claim_unprocessed_insights(
        self,
        limit: int = 10,
        min_confidence: float = 0.0,
        days_threshold: int = 7,
        pattern_type: Optional[str] = None,
    ) -> List[BehaviorInsight]:
        """
        ✅ W1: 미처리 인사이트를 한 트랜잭션 내에서 is_applied=1로 마킹 후 반환.
        동시 워커가 동일 인사이트를 중복 처리하는 Race Condition 방지.
        SELECT → UPDATE(is_applied=1) → 반환을 원자적으로 수행.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cutoff_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()
                order_clause = "ORDER BY is_verified_by_guardian DESC, confidence DESC, created_at DESC"
                if pattern_type:
                    cursor.execute(f"""
                        SELECT id FROM behavior_insights
                        WHERE is_applied = 0 AND confidence >= ?
                          AND pattern_type = ? AND created_at >= ?
                        {order_clause}
                        LIMIT ?
                    """, (min_confidence, pattern_type, cutoff_date, limit))
                else:
                    cursor.execute(f"""
                        SELECT id FROM behavior_insights
                        WHERE is_applied = 0 AND confidence >= ?
                          AND created_at >= ?
                        {order_clause}
                        LIMIT ?
                    """, (min_confidence, cutoff_date, limit))
                rows = cursor.fetchall()
                ids = [r["id"] for r in rows]
                if not ids:
                    return []
                placeholders = ",".join("?" * len(ids))
                cursor.execute(
                    f"UPDATE behavior_insights SET is_applied = 1 WHERE id IN ({placeholders})",
                    ids,
                )
                cursor.execute(
                    f"SELECT * FROM behavior_insights WHERE id IN ({placeholders})",
                    ids,
                )
                out_rows = cursor.fetchall()
                return [self._row_to_insight(row) for row in out_rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to claim unprocessed insights: {e}")
            return []

    def get_failed_experiences(self, limit: int = 20) -> List[ExperienceRecord]:
        """
        최근 실패한 경험 레코드 조회 (분석용).
        
        Args:
            limit: 최대 반환 개수
            
        Returns:
            실패한 경험 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM experience_ledger
                    WHERE is_success = 0
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                
                rows = cursor.fetchall()
                return [self._row_to_experience(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get failed experiences: {e}")
            return []

    def get_recent_ledger_entries(self, limit: int = 200) -> List[ExperienceRecord]:
        """
        ✅ verified: 경험 장부 최근 N건 조회 (LogAnalyzer·피드백 루프용).
        created_at DESC 순, latency_ms/used_tools/error_message 포함.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM experience_ledger
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
                rows = cursor.fetchall()
                return [self._row_to_experience(row) for row in rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get recent ledger entries: {e}")
            return []

    def get_ledger_entries_since(
        self,
        since_iso: str,
        limit: int = 500,
    ) -> List[ExperienceRecord]:
        """
        ✅ verified: 적용 시각 이후 경험 장부 조회 (진화 피드백 루프용).
        created_at >= since_iso 인 레코드만 반환.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM experience_ledger
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (since_iso, limit))
                rows = cursor.fetchall()
                return [self._row_to_experience(row) for row in rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get ledger entries since: {e}")
            return []

    def get_ledger_entries_before(self, before_iso: str, limit: int = 50) -> List[ExperienceRecord]:
        """
        ✅ verified: 지정 시각 이전 경험 장부 조회 (진화 적용 전 baseline용).
        created_at < before_iso, ORDER BY created_at DESC.
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM experience_ledger
                    WHERE created_at < ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (before_iso, limit))
                rows = cursor.fetchall()
                return [self._row_to_experience(row) for row in rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get ledger entries before: {e}")
            return []

    def get_monitor_flow_timeline(
        self,
        since_minutes: int = 30,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        ✅ verified: Experience Ledger, Evolution Logs, Behavior Insights를 통합하여
        시스템 '생각의 흐름' 타임라인을 반환. 프론트엔드에서 즉시 루프 렌더링 가능한 평탄화된 JSON.

        Args:
            since_minutes: 최근 N분 이내 데이터 (기본 30)
            limit: 반환할 최대 이벤트 수 (기본 50)

        Returns:
            time 정렬된 이벤트 리스트. 각 이벤트는 type (CHAT|EVOLUTION|INSIGHT|GOAL) 포함.
        """
        cutoff = (datetime.now() - timedelta(minutes=since_minutes)).isoformat()
        events: List[Dict[str, Any]] = []

        try:
            # 1. CHAT: experience_ledger (도구 이벤트, 성공 여부)
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, task_intent, is_success, used_tools, error_message,
                           latency_ms, created_at, action_steps
                    FROM experience_ledger
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (cutoff, limit))
                for row in cursor.fetchall():
                    used_tools: List[str] = []
                    if row["used_tools"]:
                        try:
                            used_tools = json.loads(row["used_tools"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    events.append({
                        "type": "CHAT",
                        "time": row["created_at"],
                        "id": row["id"],
                        "task_intent": (row["task_intent"] or "")[:500],
                        "is_success": bool(row["is_success"]),
                        "used_tools": used_tools,
                        "error_message": row["error_message"],
                        "latency_ms": row["latency_ms"],
                        "action_steps": row["action_steps"],
                    })

            # 2. EVOLUTION: evolution_logs (제안 내용, Guardian 판결, 반려 시 critique 필수)
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, target_file, proposed_code, reason, status,
                           diff_preview, created_at, updated_at
                    FROM evolution_logs
                    WHERE created_at >= ? OR updated_at >= ?
                    ORDER BY COALESCE(updated_at, created_at) DESC
                    LIMIT ?
                """, (cutoff, cutoff, limit))
                evo_rows = cursor.fetchall()
                proposal_ids = [r["id"] for r in evo_rows]

                # evolution_history에서 Guardian critique/status 조인
                hist_map: Dict[str, dict] = {}
                if proposal_ids:
                    try:
                        from mellow_link.core.database import get_evolution_history_for_proposals
                        hist_map = get_evolution_history_for_proposals(proposal_ids)
                    except Exception:
                        pass

                for row in evo_rows:
                    hist = hist_map.get(row["id"], {})
                    evo_status = row["status"]
                    audit_status = hist.get("status", "")
                    is_approved = evo_status == "APPLIED" or audit_status == "SUCCESS"
                    critique = hist.get("audit_critique", "")
                    if not is_approved and not critique and audit_status in ("REJECTED", "FAIL"):
                        critique = "(Guardian 반려 사유 없음)"
                    events.append({
                        "type": "EVOLUTION",
                        "time": row["updated_at"] or row["created_at"],
                        "id": row["id"],
                        "target_file": row["target_file"],
                        "proposed_code": (row["proposed_code"] or "")[:2000],
                        "reason": (row["reason"] or "")[:500],
                        "status": evo_status,
                        "is_approved": is_approved,
                        "critique": critique,
                        "risk_score": None,
                        "diff_preview": (row["diff_preview"] or "")[:500] if row["diff_preview"] else None,
                    })

            # 3. INSIGHT: behavior_insights (시스템이 발견한 통찰)
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, pattern_type, finding, recommendation, confidence,
                           is_applied, created_at
                    FROM behavior_insights
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (cutoff, limit))
                for row in cursor.fetchall():
                    events.append({
                        "type": "INSIGHT",
                        "time": row["created_at"],
                        "id": row["id"],
                        "pattern_type": row["pattern_type"],
                        "finding": (row["finding"] or "")[:1000],
                        "recommendation": (row["recommendation"] or "")[:500],
                        "confidence": float(row["confidence"] or 0),
                        "is_applied": bool(row["is_applied"]),
                    })

            # 4. GOAL: goals (자동 생성된 목표)
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT id, title, description, status, priority, created_at
                    FROM goals
                    WHERE created_at >= ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (cutoff, limit))
                for row in cursor.fetchall():
                    events.append({
                        "type": "GOAL",
                        "time": row["created_at"],
                        "id": row["id"],
                        "title": (row["title"] or "")[:200],
                        "description": (row["description"] or "")[:500],
                        "status": row["status"],
                        "priority": row["priority"],
                    })

            # 시간순 정렬 (최신순), limit 적용
            events.sort(key=lambda e: e.get("time", ""), reverse=True)
            return events[:limit]

        except Exception as e:
            logger.error(f"[MemoryDatabase] get_monitor_flow_timeline failed: {e}")
            return []

    def get_poor_performing_tools(
        self,
        success_rate_threshold: float = 0.5,
        avg_runtime_threshold_ms: float = 1000.0
    ) -> List[ToolStatRecord]:
        """
        성능이 저조한 도구 조회 (분석용).
        
        Args:
            success_rate_threshold: 성공률 임계치 (이하인 도구 선택)
            avg_runtime_threshold_ms: 평균 실행 시간 임계치 (이상인 도구 선택)
            
        Returns:
            성능 저조한 도구 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM tool_stats
                    WHERE (use_count > 0 AND 
                           (CAST(success_count AS REAL) / use_count < ? OR
                            avg_runtime_ms > ?))
                    ORDER BY (CAST(success_count AS REAL) / use_count) ASC, avg_runtime_ms DESC
                """, (success_rate_threshold, avg_runtime_threshold_ms))
                
                rows = cursor.fetchall()
                return [
                    ToolStatRecord(
                        tool_name=row["tool_name"],
                        use_count=row["use_count"],
                        success_count=row["success_count"],
                        last_error_msg=row["last_error_msg"],
                        avg_runtime_ms=row["avg_runtime_ms"]
                    )
                    for row in rows
                ]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get poor performing tools: {e}")
            return []

    def save_api_usage(
        self,
        provider: str,
        endpoint: str,
        token_count: int,
        cost: float = 0.0
    ) -> bool:
        """
        LLM API 사용량 기록 (Tower/Verdict/Audit 및 Guardian).
        
        Args:
            provider: google | openai | anthropic
            endpoint: 호출 엔드포인트 (예: messages.create)
            token_count: 사용 토큰 수
            cost: 예상 비용 (USD)
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                uid = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO api_usage_logs (id, provider, endpoint, token_count, cost, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (uid, provider, endpoint, token_count, cost, datetime.now().isoformat()))
                conn.commit()
                logger.debug(f"[MemoryDatabase] API usage saved: {provider} {token_count} tokens")
                return True
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save API usage: {e}")
            return False

    def get_daily_usage(self, provider: str) -> Dict[str, float]:
        """
        오늘 해당 프로바이더의 총 사용량 조회.
        
        Args:
            provider: google | openai | anthropic
            
        Returns:
            {"token_count": int, "cost": float}
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                cursor.execute("""
                    SELECT COALESCE(SUM(token_count), 0), COALESCE(SUM(cost), 0.0)
                    FROM api_usage_logs
                    WHERE provider = ? AND created_at >= ?
                """, (provider, today_start))
                row = cursor.fetchone()
                tokens = int(row[0]) if row else 0
                cost_val = float(row[1]) if row and row[1] else 0.0
                return {"token_count": tokens, "cost": cost_val}
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get daily usage: {e}")
            return {"token_count": 0, "cost": 0.0}

    def _row_to_insight(self, row: sqlite3.Row) -> BehaviorInsight:
        """SQLite Row를 BehaviorInsight로 변환."""
        # row.keys()를 사용하여 안전하게 접근
        row_keys = row.keys() if hasattr(row, "keys") else []
        
        created_at = None
        if "created_at" in row_keys and row["created_at"]:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except (ValueError, TypeError):
                pass
        
        verified = row["is_verified_by_guardian"] if "is_verified_by_guardian" in row_keys else 0
        
        return BehaviorInsight(
            id=row["id"] if "id" in row_keys else "",
            pattern_type=row["pattern_type"] if "pattern_type" in row_keys else "",
            finding=row["finding"] if "finding" in row_keys else "",
            recommendation=row["recommendation"] if "recommendation" in row_keys else "",
            confidence=float(row["confidence"]) if "confidence" in row_keys else 0.0,
            is_applied=int(row["is_applied"]) if "is_applied" in row_keys else 0,
            is_verified_by_guardian=verified,
            created_at=created_at
        )

    def add_scheduled_task(self, task: ScheduledTask) -> bool:
        """
        예약된 태스크를 데이터베이스에 추가.
        
        Args:
            task: 저장할 태스크 레코드
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                created_at = task.created_at or datetime.now()
                
                cursor.execute("""
                    INSERT INTO scheduled_tasks (
                        id, task_name, task_type, schedule_expr,
                        args_json, next_run_at, status, last_run_at,
                        consecutive_failures, root_goal_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    task.id,
                    task.task_name,
                    task.task_type,
                    task.schedule_expr,
                    task.args_json,
                    task.next_run_at.isoformat(),
                    task.status,
                    task.last_run_at.isoformat() if task.last_run_at else None,
                    task.consecutive_failures,
                    task.root_goal_id,
                    created_at.isoformat(),
                ))
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Scheduled task added: {task.id} ({task.task_name})")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to add scheduled task: {e}")
            return False

    def get_pending_tasks(self, current_time: Optional[datetime] = None) -> List[ScheduledTask]:
        """
        실행 대기 중인 태스크 조회 (next_run_at이 지난 ENABLED 태스크).
        
        Args:
            current_time: 현재 시각 (None이면 datetime.now() 사용)
            
        Returns:
            실행 대기 중인 태스크 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                now = (current_time or datetime.now()).isoformat()
                
                cursor.execute("""
                    SELECT * FROM scheduled_tasks
                    WHERE status = 'ENABLED'
                      AND next_run_at <= ?
                    ORDER BY next_run_at ASC
                """, (now,))
                
                rows = cursor.fetchall()
                return [self._row_to_scheduled_task(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get pending tasks: {e}")
            return []

    def update_task_result(
        self,
        task_id: str,
        next_run_at: Optional[datetime] = None,
        status: Optional[str] = None,
        consecutive_failures: Optional[int] = None
    ) -> bool:
        """
        태스크 실행 결과 업데이트.
        
        Args:
            task_id: 태스크 ID
            next_run_at: 다음 실행 시각 (None이면 갱신 안 함)
            status: 새 상태 (None이면 갱신 안 함)
            consecutive_failures: 연속 실패 횟수 (None이면 갱신 안 함)
            
        Returns:
            업데이트 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                updates = []
                params = []
                
                if next_run_at:
                    updates.append("next_run_at = ?")
                    params.append(next_run_at.isoformat())
                
                if status:
                    updates.append("status = ?")
                    params.append(status)
                
                if consecutive_failures is not None:
                    updates.append("consecutive_failures = ?")
                    params.append(consecutive_failures)
                
                # last_run_at은 항상 현재 시각으로 갱신
                updates.append("last_run_at = ?")
                params.append(datetime.now().isoformat())
                
                if not updates:
                    return True
                
                params.append(task_id)
                
                cursor.execute(f"""
                    UPDATE scheduled_tasks
                    SET {', '.join(updates)}
                    WHERE id = ?
                """, params)
                
                conn.commit()
                logger.debug(f"[MemoryDatabase] Task result updated: {task_id}")
                return True
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to update task result: {e}")
            return False

    def get_all_scheduled_tasks(self, status: Optional[str] = None) -> List[ScheduledTask]:
        """
        모든 예약된 태스크 조회.
        
        Args:
            status: 상태 필터 (None이면 모든 상태)
            
        Returns:
            태스크 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                if status:
                    cursor.execute("""
                        SELECT * FROM scheduled_tasks
                        WHERE status = ?
                        ORDER BY next_run_at ASC
                    """, (status,))
                else:
                    cursor.execute("""
                        SELECT * FROM scheduled_tasks
                        ORDER BY next_run_at ASC
                    """)
                
                rows = cursor.fetchall()
                return [self._row_to_scheduled_task(row) for row in rows]
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get scheduled tasks: {e}")
            return []

    def _row_to_scheduled_task(self, row: sqlite3.Row) -> ScheduledTask:
        """SQLite Row를 ScheduledTask로 변환."""
        next_run_at = None
        last_run_at = None
        created_at = None
        
        if row["next_run_at"]:
            try:
                next_run_at = datetime.fromisoformat(row["next_run_at"])
            except ValueError:
                pass
        
        if row["last_run_at"]:
            try:
                last_run_at = datetime.fromisoformat(row["last_run_at"])
            except ValueError:
                pass
        
        if row["created_at"]:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except ValueError:
                pass
        
        # 새 컬럼 지원 (없을 수 있음)
        row_keys = row.keys()
        consecutive_failures = row["consecutive_failures"] if "consecutive_failures" in row_keys else 0
        root_goal_id = row["root_goal_id"] if "root_goal_id" in row_keys else None
        
        return ScheduledTask(
            id=row["id"],
            task_name=row["task_name"],
            task_type=row["task_type"],
            schedule_expr=row["schedule_expr"],
            args_json=row["args_json"],
            next_run_at=next_run_at or datetime.now(),
            status=row["status"],
            last_run_at=last_run_at,
            consecutive_failures=consecutive_failures,
            root_goal_id=root_goal_id,
            created_at=created_at
        )

    def save_metric(
        self,
        metric_id: str,
        category: str,
        value: float,
        unit: str,
        timestamp: Optional[datetime] = None
    ) -> bool:
        """
        성능 지표를 데이터베이스에 저장.
        
        Args:
            metric_id: 지표 고유 ID (UUID)
            category: 지표 카테고리 (TOOL, LATENCY, TOKEN, GOAL)
            value: 지표 값
            unit: 단위 (%, ms, tokens 등)
            timestamp: 기록 시각 (None이면 현재 시각)
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                record_time = timestamp or datetime.now()
                
                cursor.execute("""
                    INSERT INTO performance_metrics (
                        metric_id, category, value, unit, timestamp
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    metric_id,
                    category,
                    value,
                    unit,
                    record_time.isoformat(),
                ))
                
                conn.commit()
                logger.debug(
                    f"[MemoryDatabase] Metric saved: {category}={value}{unit} "
                    f"({metric_id})"
                )
                return True
                
        except Exception as e:
            err_str = str(e)
            # CHECK constraint 실패 시 스키마 마이그레이션 시도
            if "CHECK constraint" in err_str and category not in ("TOOL", "LATENCY", "TOKEN", "GOAL"):
                try:
                    return self._migrate_and_retry_save_metric(
                        metric_id, category, value, unit, timestamp
                    )
                except Exception as e2:
                    logger.error(f"[MemoryDatabase] Metric migration failed: {e2}")
                    return False
            logger.error(f"[MemoryDatabase] Failed to save metric: {e}")
            return False

    def _migrate_and_retry_save_metric(
        self,
        metric_id: str,
        category: str,
        value: float,
        unit: str,
        timestamp: Optional[datetime] = None,
    ) -> bool:
        """
        Phase 5: performance_metrics 테이블의 CHECK constraint를 확장하여
        신규 KPI 카테고리를 지원하도록 마이그레이션.
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 기존 테이블 백업 → 새 스키마로 재생성
                cursor.execute("ALTER TABLE performance_metrics RENAME TO _pm_backup")
                cursor.execute("""
                    CREATE TABLE performance_metrics (
                        metric_id TEXT PRIMARY KEY,
                        category TEXT NOT NULL CHECK(category IN (
                            'TOOL', 'LATENCY', 'TOKEN', 'GOAL',
                            'TASK_SUCCESS', 'CRITICAL_ERROR',
                            'VERIFY_COVERAGE', 'ERROR_RECURRENCE',
                            'TTFT_MS', 'TPS', 'TPS_APPROX', 'TOKENS_IN', 'TOKENS_OUT', 'OBSERVATION_VIOLATION',
                            'INFER_MS', 'TTFT_MEASURED'
                        )),
                        value REAL NOT NULL,
                        unit TEXT NOT NULL,
                        timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                cursor.execute("""
                    INSERT INTO performance_metrics
                    SELECT * FROM _pm_backup
                """)
                cursor.execute("DROP TABLE _pm_backup")
                # 재시도
                record_time = timestamp or datetime.now()
                cursor.execute("""
                    INSERT INTO performance_metrics (
                        metric_id, category, value, unit, timestamp
                    ) VALUES (?, ?, ?, ?, ?)
                """, (metric_id, category, value, unit, record_time.isoformat()))
                conn.commit()
                logger.info("[MemoryDatabase] performance_metrics schema migrated + metric saved: %s", category)
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] _migrate_and_retry_save_metric failed: %s", e)
            return False

    def get_recent_metrics(
        self,
        category: Optional[str] = None,
        metric_id: Optional[str] = None,
        days: int = 7,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        최근 성능 지표 조회.
        
        Args:
            category: 카테고리 필터 (None이면 모든 카테고리)
            metric_id: 특정 metric_id로 필터링 (None이면 모든 metric_id)
            days: 최근 N일 이내 데이터만 조회
            limit: 최대 반환 개수
            
        Returns:
            지표 레코드 리스트 (각 레코드는 dict)
        """
        try:
            # 디버깅: DB 경로 확인
            db_path = getattr(self, 'db_path', 'unknown')
            logger.debug(f"[MemoryDatabase] get_recent_metrics: db_path={db_path}, category={category}, metric_id={metric_id}, days={days}, limit={limit}")
            
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()
                logger.debug(f"[MemoryDatabase] get_recent_metrics: cutoff_date={cutoff_date}")
                
                # 조건 구성
                conditions = ["timestamp >= ?"]
                params = [cutoff_date]
                
                if category:
                    conditions.append("category = ?")
                    params.append(category)
                
                if metric_id:
                    conditions.append("metric_id = ?")
                    params.append(metric_id)
                
                where_clause = " AND ".join(conditions)
                params.append(limit)
                
                query = f"""
                    SELECT metric_id, category, value, unit, timestamp
                    FROM performance_metrics
                    WHERE {where_clause}
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                
                logger.debug(f"[MemoryDatabase] get_recent_metrics: query={query}, params={params}")
                cursor.execute(query, params)
                
                rows = cursor.fetchall()
                logger.debug(f"[MemoryDatabase] get_recent_metrics: found {len(rows)} rows")
                
                result = [
                    {
                        "metric_id": row["metric_id"],
                        "category": row["category"],
                        "value": row["value"],
                        "unit": row["unit"],
                        "timestamp": datetime.fromisoformat(row["timestamp"])
                    }
                    for row in rows
                ]
                
                # 결과가 비어있을 때 경고
                if not result:
                    # 테이블에 데이터가 있는지 확인
                    cursor.execute("SELECT COUNT(*) as count FROM performance_metrics")
                    total_count = cursor.fetchone()["count"]
                    logger.warning(
                        f"[MemoryDatabase] get_recent_metrics: No metrics found. "
                        f"Total metrics in DB: {total_count}, "
                        f"Filter: category={category}, metric_id={metric_id}, days={days}"
                    )
                
                return result
                
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get recent metrics: {e}", exc_info=True)
            return []

    def save_dynamic_tool(self, record: "DynamicToolRecord") -> bool:
        """
        동적 도구 레코드 저장.
        
        Args:
            record: 저장할 동적 도구 레코드
            
        Returns:
            저장 성공 여부
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                created_at = record.created_at or datetime.now()
                cursor.execute("""
                    INSERT INTO dynamic_tools (
                        id, tool_name, description, code, parameters_json,
                        author_agent_id, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        tool_name = excluded.tool_name,
                        description = excluded.description,
                        code = excluded.code,
                        parameters_json = excluded.parameters_json,
                        author_agent_id = excluded.author_agent_id,
                        status = excluded.status
                """, (
                    record.id,
                    record.tool_name,
                    record.description,
                    record.code,
                    record.parameters_json,
                    record.author_agent_id,
                    record.status,
                    created_at.isoformat(),
                ))
                conn.commit()
                logger.debug(f"[MemoryDatabase] Dynamic tool saved: {record.tool_name} ({record.id})")
                return True
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to save dynamic tool: {e}")
            return False

    def get_dynamic_tool(self, tool_id: str) -> Optional["DynamicToolRecord"]:
        """
        동적 도구 1건 조회.
        
        Args:
            tool_id: 동적 도구 ID
            
        Returns:
            DynamicToolRecord 또는 None
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM dynamic_tools WHERE id = ?", (tool_id,)
                )
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_dynamic_tool(row)
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get dynamic tool: {e}")
            return None

    def get_dynamic_tools_by_status(
        self,
        status: Optional[str] = None,
        limit: int = 100
    ) -> List["DynamicToolRecord"]:
        """
        상태별 동적 도구 목록 조회.
        
        Args:
            status: PENDING, VERIFIED, REJECTED 중 하나 (None이면 전체)
            limit: 최대 반환 개수
            
        Returns:
            동적 도구 레코드 리스트
        """
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if status is not None:
                    cursor.execute("""
                        SELECT * FROM dynamic_tools WHERE status = ?
                        ORDER BY created_at DESC LIMIT ?
                    """, (status, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM dynamic_tools
                        ORDER BY created_at DESC LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                return [self._row_to_dynamic_tool(row) for row in rows]
        except Exception as e:
            logger.error(f"[MemoryDatabase] Failed to get dynamic tools: {e}")
            return []

    def _row_to_dynamic_tool(self, row: sqlite3.Row) -> "DynamicToolRecord":
        """SQLite Row를 DynamicToolRecord로 변환."""
        created_at = None
        if row["created_at"]:
            try:
                created_at = datetime.fromisoformat(row["created_at"])
            except ValueError:
                pass
        return DynamicToolRecord(
            id=row["id"],
            tool_name=row["tool_name"],
            description=row["description"],
            code=row["code"],
            parameters_json=row["parameters_json"],
            author_agent_id=row["author_agent_id"],
            status=row["status"],
            created_at=created_at,
        )

    def save_evolution_log(self, record: "EvolutionLogRecord") -> bool:
        """진화 제안서 저장 (INSERT or UPDATE). root_goal_id 포함."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                created_at = record.created_at or datetime.now()
                updated_at = record.updated_at or datetime.now()
                root_goal_id = getattr(record, "root_goal_id", None) or ""
                cursor.execute("""
                    INSERT INTO evolution_logs (
                        id, target_file, proposed_code, reason, diff_preview,
                        status, previous_content, author_agent_id, created_at, updated_at,
                        root_goal_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        target_file = excluded.target_file,
                        proposed_code = excluded.proposed_code,
                        reason = excluded.reason,
                        diff_preview = excluded.diff_preview,
                        status = excluded.status,
                        previous_content = excluded.previous_content,
                        author_agent_id = excluded.author_agent_id,
                        updated_at = excluded.updated_at,
                        root_goal_id = excluded.root_goal_id
                """, (
                    record.id, record.target_file, record.proposed_code, record.reason,
                    record.diff_preview, record.status, record.previous_content,
                    record.author_agent_id, created_at.isoformat(), updated_at.isoformat(),
                    root_goal_id,
                ))
                conn.commit()
                logger.debug("[MemoryDatabase] Evolution log saved: %s", record.id)
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to save evolution log: %s", e)
            return False

    def get_evolution_log(self, log_id: str) -> Optional["EvolutionLogRecord"]:
        """진화 제안서 1건 조회."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM evolution_logs WHERE id = ?", (log_id,))
                row = cursor.fetchone()
                if not row:
                    return None
                return self._row_to_evolution_log(row)
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to get evolution log: %s", e)
            return None

    def get_evolution_logs_by_status(
        self,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List["EvolutionLogRecord"]:
        """상태별 진화 제안서 목록 조회."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if status is not None:
                    cursor.execute("""
                        SELECT * FROM evolution_logs WHERE status = ?
                        ORDER BY updated_at DESC LIMIT ?
                    """, (status, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM evolution_logs
                        ORDER BY updated_at DESC LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                return [self._row_to_evolution_log(row) for row in rows]
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to get evolution logs: %s", e)
            return []

    def update_evolution_log_status(
        self,
        log_id: str,
        status: str,
        previous_content: Optional[str] = None,
        diff_preview: Optional[str] = None
    ) -> bool:
        """진화 제안서 상태 갱신 (롤백 시 previous_content 복원용). APPLIED 시 applied_at 설정."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                updates = ["status = ?", "updated_at = ?"]
                params: List[Any] = [status, datetime.now().isoformat()]
                if previous_content is not None:
                    updates.append("previous_content = ?")
                    params.append(previous_content)
                if diff_preview is not None:
                    updates.append("diff_preview = ?")
                    params.append(diff_preview)
                if status == "APPLIED":
                    updates.append("applied_at = ?")
                    params.append(datetime.now().isoformat())
                params.append(log_id)
                cursor.execute(
                    f"UPDATE evolution_logs SET {', '.join(updates)} WHERE id = ?",
                    params
                )
                conn.commit()
                logger.debug("[MemoryDatabase] Evolution log status updated: %s -> %s", log_id, status)
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to update evolution log status: %s", e)
            return False

    def set_evolution_feedback_failed(self, log_id: str) -> bool:
        """✅ verified: 적용 후 에러율 급증 시 FAILED 취급·롤백 권고용 플래그 설정."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE evolution_logs SET feedback_failed = 1, updated_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), log_id),
                )
                conn.commit()
                logger.info("[MemoryDatabase] Evolution feedback_failed set: %s", log_id)
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to set evolution feedback_failed: %s", e)
            return False

    def save_autonomous_work_result(self, record: "AutonomousWorkResult") -> bool:
        """자율 작업 결과 저장."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # 타입 안전성: 모든 값을 명시적으로 변환
                created_at = record.created_at
                if created_at is None:
                    created_at = datetime.now()
                elif isinstance(created_at, str):
                    try:
                        created_at = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        created_at = datetime.now()
                
                updated_at = record.updated_at
                if updated_at is None:
                    updated_at = datetime.now()
                elif isinstance(updated_at, str):
                    try:
                        updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    except (ValueError, AttributeError):
                        updated_at = datetime.now()
                
                # ethics_approved는 반드시 int여야 함
                try:
                    ethics_approved = int(record.ethics_approved) if record.ethics_approved is not None else 0
                except (ValueError, TypeError):
                    logger.warning("[MemoryDatabase] Invalid ethics_approved value: %s (type: %s), defaulting to 0", 
                                 record.ethics_approved, type(record.ethics_approved))
                    ethics_approved = 0
                
                # 모든 값을 안전하게 변환
                # SQLite의 DATETIME은 실제로 TEXT로 저장되므로 문자열로 변환
                created_at_str = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at) if created_at else datetime.now().isoformat()
                updated_at_str = updated_at.isoformat() if isinstance(updated_at, datetime) else str(updated_at) if updated_at else datetime.now().isoformat()
                
                values = (
                    str(record.id) if record.id else "",
                    str(record.task_type) if record.task_type else "",
                    str(record.tools_created) if record.tools_created is not None else None,
                    str(record.info_collected) if record.info_collected is not None else None,
                    str(record.ethics_review) if record.ethics_review is not None else None,
                    ethics_approved,  # INTEGER 타입
                    str(record.status) if record.status else "PENDING",
                    created_at_str,  # TEXT (DATETIME은 SQLite에서 TEXT로 저장)
                    updated_at_str,  # TEXT (DATETIME은 SQLite에서 TEXT로 저장)
                )
                
                # 디버깅: 값 타입 로깅
                logger.debug(
                    "[MemoryDatabase] Saving autonomous work result:\n"
                    "  id=%s (type: %s)\n"
                    "  ethics_approved=%s (type: %s, value: %s)\n"
                    "  status=%s (type: %s)\n"
                    "  created_at=%s (type: %s)\n"
                    "  updated_at=%s (type: %s)",
                    record.id, type(record.id).__name__,
                    record.ethics_approved, type(record.ethics_approved).__name__, ethics_approved,
                    record.status, type(record.status).__name__,
                    created_at_str, type(created_at_str).__name__,
                    updated_at_str, type(updated_at_str).__name__
                )
                
                # 기존 레코드 확인 후 INSERT 또는 UPDATE
                cursor.execute("SELECT id FROM autonomous_work_results WHERE id = ?", (str(record.id),))
                exists = cursor.fetchone()
                
                if exists:
                    # UPDATE: 기존 레코드 업데이트 (created_at은 유지)
                    cursor.execute("""
                        UPDATE autonomous_work_results SET
                            task_type = ?,
                            tools_created = ?,
                            info_collected = ?,
                            ethics_review = ?,
                            ethics_approved = ?,
                            status = ?,
                            updated_at = ?
                        WHERE id = ?
                    """, (
                        values[1],  # task_type
                        values[2],  # tools_created
                        values[3],  # info_collected
                        values[4],  # ethics_review
                        values[5],  # ethics_approved
                        values[6],  # status
                        values[8],  # updated_at
                        values[0],  # id
                    ))
                else:
                    # INSERT: 새 레코드 삽입
                    cursor.execute("""
                        INSERT INTO autonomous_work_results
                        (id, task_type, tools_created, info_collected, ethics_review, ethics_approved, status, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, values)
                conn.commit()
                return True
        except sqlite3.IntegrityError as e:
            error_msg = str(e)
            logger.error(
                "[MemoryDatabase] IntegrityError saving autonomous work result: %s\n"
                "  Record details: id=%s, task_type=%s, ethics_approved=%s (type: %s), status=%s\n"
                "  Values: %s",
                error_msg,
                record.id,
                record.task_type,
                record.ethics_approved,
                type(record.ethics_approved).__name__,
                record.status,
                values if 'values' in locals() else "N/A"
            )
            # datatype mismatch인 경우 더 자세한 정보 로깅
            if "datatype mismatch" in error_msg.lower() or "type mismatch" in error_msg.lower():
                logger.error(
                    "[MemoryDatabase] Datatype mismatch details:\n"
                    "  id: %s (type: %s, value: %s)\n"
                    "  task_type: %s (type: %s, value: %s)\n"
                    "  ethics_approved: %s (type: %s, converted: %s, value: %s)\n"
                    "  status: %s (type: %s, value: %s)\n"
                    "  created_at: %s (type: %s, value: %s)\n"
                    "  updated_at: %s (type: %s, value: %s)\n"
                    "  All values tuple: %s",
                    record.id, type(record.id).__name__, str(record.id)[:50],
                    record.task_type, type(record.task_type).__name__, str(record.task_type)[:50],
                    record.ethics_approved, type(record.ethics_approved).__name__, 
                    ethics_approved if 'ethics_approved' in locals() else "N/A", 
                    str(ethics_approved) if 'ethics_approved' in locals() else "N/A",
                    record.status, type(record.status).__name__, str(record.status)[:50],
                    record.created_at, type(record.created_at).__name__ if record.created_at else "None",
                    created_at_str if 'created_at_str' in locals() else str(record.created_at)[:50] if record.created_at else "None",
                    record.updated_at, type(record.updated_at).__name__ if record.updated_at else "None",
                    updated_at_str if 'updated_at_str' in locals() else str(record.updated_at)[:50] if record.updated_at else "None",
                    values if 'values' in locals() else "N/A"
                )
            return False
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to save autonomous work result: %s (record: id=%s)", e, record.id, exc_info=True)
            return False

    def get_autonomous_work_results_by_status(
        self, status: Optional[str] = None, limit: int = 50
    ) -> List["AutonomousWorkResult"]:
        """자율 작업 결과 목록 조회 (상태별)."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if status:
                    cursor.execute("""
                        SELECT * FROM autonomous_work_results WHERE status = ?
                        ORDER BY updated_at DESC LIMIT ?
                    """, (status, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM autonomous_work_results
                        ORDER BY updated_at DESC LIMIT ?
                    """, (limit,))
                rows = cursor.fetchall()
                return [self._row_to_autonomous_work_result(row) for row in rows]
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to get autonomous work results: %s", e)
            return []

    def update_autonomous_work_status(self, record_id: str, status: str) -> bool:
        """자율 작업 결과 상태 갱신."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE autonomous_work_results SET status = ?, updated_at = ?
                    WHERE id = ?
                """, (status, datetime.now().isoformat(), record_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to update autonomous work status: %s", e)
            return False

    def update_autonomous_work_output(self, record_id: str, status: str, output: Optional[str] = None) -> bool:
        """자율 작업 결과 상태 및 실행 결과 갱신."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE autonomous_work_results SET status = ?, output = ?, updated_at = ?
                    WHERE id = ?
                """, (status, output or "", datetime.now().isoformat(), record_id))
                conn.commit()
                return True
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to update autonomous work output: %s", e)
            return False

    def get_autonomous_work_result_by_id(self, record_id: str) -> Optional["AutonomousWorkResult"]:
        """ID로 자율 작업 결과 조회."""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM autonomous_work_results WHERE id = ?", (record_id,))
                row = cursor.fetchone()
                return self._row_to_autonomous_work_result(row) if row else None
        except Exception as e:
            logger.error("[MemoryDatabase] Failed to get autonomous work result: %s", e)
            return None

    def _row_to_autonomous_work_result(self, row: sqlite3.Row) -> "AutonomousWorkResult":
        """SQLite Row를 AutonomousWorkResult로 변환."""
        def _dt(s: Any) -> Optional[datetime]:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return None
        keys = row.keys() if hasattr(row, "keys") else []
        def _get(k: str, default: Any = None) -> Any:
            return row[k] if k in keys else default
        return AutonomousWorkResult(
            id=row["id"],
            task_type=row["task_type"],
            tools_created=_get("tools_created"),
            info_collected=_get("info_collected"),
            ethics_review=_get("ethics_review"),
            ethics_approved=int(_get("ethics_approved", 0) or 0),
            status=row["status"],
            output=_get("output"),
            created_at=_dt(_get("created_at")),
            updated_at=_dt(_get("updated_at")),
        )

    def _row_to_evolution_log(self, row: sqlite3.Row) -> "EvolutionLogRecord":
        """SQLite Row를 EvolutionLogRecord로 변환."""
        def _dt(s: Any) -> Optional[datetime]:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s)
            except ValueError:
                return None
        keys = row.keys() if hasattr(row, "keys") else []
        def _get(k: str, default: Any = None) -> Any:
            return row[k] if k in keys else default
        return EvolutionLogRecord(
            id=row["id"],
            target_file=row["target_file"],
            proposed_code=row["proposed_code"],
            reason=row["reason"],
            diff_preview=_get("diff_preview"),
            status=row["status"],
            previous_content=_get("previous_content"),
            author_agent_id=_get("author_agent_id"),
            created_at=_dt(_get("created_at")),
            updated_at=_dt(_get("updated_at")),
            applied_at=_dt(_get("applied_at")),
            feedback_failed=int(_get("feedback_failed") or 0),
            root_goal_id=_get("root_goal_id") or None,
        )

    @staticmethod
    def compute_task_hash(task_intent: str, context_summary: str) -> str:
        """
        작업 해시 계산 (유사 작업 식별용).
        
        Args:
            task_intent: 작업 의도
            context_summary: 컨텍스트 요약
            
        Returns:
            해시 문자열
        """
        combined = f"{task_intent}|{context_summary}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


# =============================================================================
# 싱글톤 인스턴스
# =============================================================================

_db_instance: Optional[MemoryDatabase] = None


def get_memory_db(db_path: Optional[Path] = None) -> MemoryDatabase:
    """
    MemoryDatabase 싱글톤 인스턴스 반환.
    
    Args:
        db_path: 데이터베이스 파일 경로 (첫 호출 시에만 적용)
        
    Returns:
        MemoryDatabase 인스턴스
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = MemoryDatabase(db_path)
    return _db_instance
