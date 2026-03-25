# rebuild_assistant

`rebuild_assistant`는 JSP/Java/SQL 계열 레거시 기능을 단일 기능 또는 단일 페이지 단위로 분석하고, 현대화 재구성 전략과 초안을 생성하는 모듈입니다.

## 현재 로드맵 상태

### 최종 목적

`rebuild_assistant`의 목표 흐름은 아래와 같습니다.

- 레거시 코드
- `feature mode` 분류
- 규칙 추출
- 회사 규칙 적용
- 현대화 설계

### 현재 단계

현재 구현 상태는 아래와 같습니다.

- `feature mode` 분류 완료
- `feature mode` 기반 현대화 설계 초안 생성 가능
- 규칙 추출 미구현
- 회사 규칙 적용 미구현

### 다음 단계

다음 구현 목표는 아래와 같습니다.

- mode별 규칙 추출 구조 설계
- 추출된 규칙을 회사 규칙 레이어와 연결할 입력 계약 설계

## 목적

- 레거시 화면/코드/SQL 자산 분석
- 기능 성격 분류
  - `status_permissions`
  - `search_filters`
  - `save_validation`
- 레이어별 재구성 전략 제안
- 구조화된 초안 생성

V0 범위는 전체 시스템 마이그레이션이 아니라 단일 기능 수준의 재구성 초안입니다.

## 시작 경로

- UI: `/modules/rebuild_assistant`
- Run API: `POST /modules/rebuild_assistant/runs`

## 입력

요청 스키마는 아래 필드를 사용합니다.

- `goal: str`
  - 필수
  - `goal.strip()` 기준 최소 8자
- `assets`
  - `source_code`
  - `database_schema`
  - `sql_queries`
  - `ui_template`
  - `framework_info`
- `constraints: list[str]`
- `temp_session_id: str | None`

`assets` 또는 `temp_session_id` 중 하나는 반드시 필요합니다.

## 업로드 보조 기능

UI에서는 기존 temp upload 흐름을 그대로 재사용합니다.

- 업로드 파일은 모두 temp context에도 저장됩니다.
- 일부 파일은 브라우저에서 자동 분류되어 textarea에 보조 입력됩니다.
  - `goal.txt` -> `goal`
  - `constraints.txt` -> `constraints`
  - `*.jsp`, `*.html` -> `ui_template`
  - `*.java`, `*.js`, `*.ts`, `*.xml` -> `source_code`
  - `schema*.sql`, `ddl*.sql`, `CREATE TABLE` 중심 SQL -> `database_schema`
  - `query*.sql`, `mapper*.sql`, query-heavy SQL -> `sql_queries`
  - `framework*`, `readme*`, `.md`, `.properties`, `.yml`, `.yaml` -> `framework_info`
- 사용자가 이미 직접 입력한 textarea는 자동 분류가 덮어쓰지 않습니다.

## 분류 모드

현재 `service.py`는 입력 자산에서 기능 신호를 추출해 `primary_feature_mode`를 고릅니다.

- `status_permissions`
  - 역할/상태 기반 액션 노출
  - approve/reject/resubmit
  - 상태 전이 규칙
- `search_filters`
  - 검색 폼
  - 복수 필터 파라미터
  - 결과 테이블/리스트
  - 동적 쿼리 조합
  - paging/sort/filter state
- `save_validation`
  - 필수값 검증
  - 중복 체크
  - 저장 가드
  - 예외 기반 검증 흐름

결과 전략과 초안은 `primary_feature_mode`를 중심으로 작성되고, 보조 신호는 필요한 범위에서만 일부 반영됩니다.

## 출력 계약

`structured_result`는 항상 같은 shape를 유지합니다.

- `one_line_conclusion: str`
- `analysis_summary: list[str]`
- `rebuild_strategy: list[str]`
- `layer_reconstruction`
  - `database: list[str]`
  - `backend: list[str]`
  - `frontend: list[str]`
- `recomposition_draft`
  - `database: list[str]`
  - `backend: list[str]`
  - `frontend: list[str]`
- `risks: list[str]`
- `confidence: float`
- `missing_context: list[str]`

`run_finished` payload에는 아래 메타도 함께 포함됩니다.

- `primary_feature_mode`
- `secondary_feature_mode`
- `scope_limited`
- `needs_more_input`

## 진행 단계

내부 raw todo는 5단계입니다.

- `B1` prepare
- `B2` analyze
- `B3` design
- `B4` draft
- `B5` finalize

사용자 콘솔의 3단계 진행률 매핑은 아래와 같습니다.

- `준비`: `B1`, `B2`
- `처리`: `B3`, `B4`
- `완료`: `B5`

## 테스트

회귀 테스트는 아래 파일에 포함되어 있습니다.

- [mellow_link/tests/test_module_registry_and_runs.py](/D:/AI_Project/mellow_link/tests/test_module_registry_and_runs.py)

현재 테스트는 아래를 검증합니다.

- 모듈 등록 및 run metadata
- 입력 검증
- `structured_result` shape
- feature mode 분류 회귀
- `status_permissions` / `search_filters` / `save_validation` 샘플의 결론 문구
- todo 매핑과 runner payload
