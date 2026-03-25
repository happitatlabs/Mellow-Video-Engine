# Sample SQLite Schema for SQL-AI Decision Engine MVP

## 목적
- `/analyze`의 SQL 조회를 mock이 아닌 실제 SQLite 데이터로 검증
- Rule 임계치(`refund_rate > 0.07`, `inquiry_growth > 0.15`, `churn_rate > 0.08`)를 통과/미통과하는 시나리오 구성

## DB 파일
- `sample_data/decision_engine_sample.db`
- 생성 스크립트: `sample_data/seed_sample_db.py`

## 테이블 정의 (CREATE TABLE)
```sql
CREATE TABLE customer_service_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    refund_rate REAL NOT NULL,
    inquiry_growth REAL NOT NULL,
    PRIMARY KEY (date, segment)
);

CREATE TABLE customer_churn_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    churn_rate REAL NOT NULL,
    refund_rate REAL NOT NULL,
    PRIMARY KEY (date, segment)
);

CREATE TABLE inquiry_metrics (
    date TEXT NOT NULL,
    segment TEXT NOT NULL,
    inquiry_growth REAL NOT NULL,
    inquiry_count INTEGER NOT NULL,
    PRIMARY KEY (date, segment)
);
```

## Seed 데이터 특징
- 기간: `2026-03-01 ~ 2026-03-31`
- 세그먼트: `all`, `premium`, `general`
- 케이스:
  - normal: 2026-03-05 (`all`) 임계치 모두 이하
  - warning: 2026-03-15 (`all`) `inquiry_growth`만 초과
  - high_risk: 2026-03-28 (`all`) `refund_rate`, `inquiry_growth`, `churn_rate` 초과

## 템플릿 연동
- `sql_templates/refund_analysis.sql` -> `customer_service_metrics`
- `sql_templates/churn_analysis.sql` -> `customer_churn_metrics`
- `sql_templates/inquiry_analysis.sql` -> `inquiry_metrics`
- 공통 파라미터: `:start_date`, `:end_date`, `:segment`

## 초기화/재생성
```bash
cd D:/AI_Project/mellow_link/sql_ai_decision_engine
python sample_data/seed_sample_db.py
```

## 검증 쿼리 예시
```sql
-- normal
SELECT refund_rate, inquiry_growth
FROM customer_service_metrics
WHERE date BETWEEN '2026-03-01' AND '2026-03-10' AND segment = 'all'
ORDER BY date DESC LIMIT 1;

-- warning
SELECT refund_rate, inquiry_growth
FROM customer_service_metrics
WHERE date BETWEEN '2026-03-01' AND '2026-03-20' AND segment = 'all'
ORDER BY date DESC LIMIT 1;

-- high_risk
SELECT churn_rate, refund_rate
FROM customer_churn_metrics
WHERE date BETWEEN '2026-03-01' AND '2026-03-31' AND segment = 'all'
ORDER BY date DESC LIMIT 1;
```

## sql_executor 연결 포인트
- `app/services/sql_executor.py`
  - `MockSQLExecutor`
  - `RealSQLiteExecutor`
  - `build_sql_executor(use_mock_sql, database_url)`
- 설정:
  - `USE_MOCK_SQL` (기본 `false`)
  - `DATABASE_URL` (기본 `sample_data/decision_engine_sample.db`)
- 설정 파일: `app/config/settings.py`
