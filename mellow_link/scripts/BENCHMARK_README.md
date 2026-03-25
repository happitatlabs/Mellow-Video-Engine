# Mode Distribution Benchmark

Mellow-Link의 자동 모드 선택(fast/thinking) 분포를 벤치마크하는 자동화된 러너입니다.

## 개요

이 벤치마크는 `/chat/ask` 엔드포인트에 30개의 프롬프트를 `mode="auto"`로 전송하여:
- 모드 선택 분포 (fast vs thinking)
- 성능 메트릭 (TTFT, TPS, INFER_MS 등)
- Fallback 트리거 횟수
- 이상치 감지

를 측정하고 리포트를 생성합니다.

## 요구사항

- Mellow-Link 서버가 실행 중이어야 합니다
- Python 3.8+
- 필요한 패키지: `aiohttp`

## 사용법

**실행 위치**: 프로젝트 루트(`d:\AI_Project`)에서 실행할 때는 `mellow_link/scripts/...`를 사용하고, `mellow_link` 디렉터리에서 실행할 때는 `scripts/...`를 사용합니다. 리포트는 항상 `mellow_link/outputs/bench/`에 저장됩니다.

### 기본 실행

```bash
# 프로젝트 루트에서
python mellow_link/scripts/run_mode_benchmark.py

# mellow_link 디렉터리에서
python scripts/run_mode_benchmark.py
```

### 옵션 지정

```bash
# API URL 지정
python mellow_link/scripts/run_mode_benchmark.py --api-url http://localhost:8000

# 인증 토큰 지정 (필요한 경우)
python mellow_link/scripts/run_mode_benchmark.py --auth-token YOUR_TOKEN
```

### 리포트 분석

```bash
# 저장된 리포트 분석 (상대 경로는 mellow_link/outputs/bench 기준)
python mellow_link/scripts/analyze_mode_benchmark.py --report outputs/bench/mode_benchmark_20240218_120000.json

# DB에서 직접 메트릭 조회
python mellow_link/scripts/analyze_mode_benchmark.py --query-db \
    --start-time "2024-02-18 12:00:00" \
    --end-time "2024-02-18 12:30:00"
```

## 출력

### 리포트 파일

벤치마크 실행 후 다음 위치에 JSON 리포트가 저장됩니다:

```
mellow_link/outputs/bench/mode_benchmark_<timestamp>.json
```

### 리포트 내용

- **요청별 상세 데이터**: 각 요청의 프롬프트, 선택된 모드, 메트릭 등
- **집계 통계**: 모드 분포, fallback 횟수, 성능 메트릭 통계 (p50, p95, mean 등)
- **이상치**: INFER_MS 상위 5개 요청
- **경고**: 다음 조건에서 경고 생성
  - Fast 모드 사용률 < 20%
  - Fast fallback 트리거 >= 5회
  - INFER_MS p95/p50 비율 >= 3.0

### 콘솔 출력

실행 중 각 요청의 진행 상황과 최종 요약이 콘솔에 출력됩니다.

## 프롬프트 구성

벤치마크는 다음 30개 프롬프트를 사용합니다:

- **10개 빠른 응답 예상**: 간단한 인사, 날씨 질문 등
- **10개 도구 필요**: 파일 시스템 조회, 시스템 정보 등
- **10개 깊은 사고**: 철학적 질문, 복잡한 분석 등

## 메트릭 수집

벤치마크는 다음 메트릭을 수집합니다:

### SSE done 이벤트에서
- `session_id`
- `message_id`
- `processing_time`
- `rag_used`

### ChatMessage 테이블에서
- `selected_mode`
- `auto_selected`

### performance_metrics 테이블에서
- `TTFT_MS`: Time To First Token (ms)
- `TTFT_MEASURED`: TTFT 측정 여부
- `TPS`: Tokens Per Second
- `TPS_APPROX`: 근사 TPS
- `INFER_MS`: 추론 시간 (ms)
- `FAST_FALLBACK_TRIGGERED`: Fast fallback 트리거 여부
- `FAST_FALLBACK_BLOCKED`: Fast fallback 차단 여부

## 주의사항

1. **순차 실행**: 요청 간 간섭을 피하기 위해 순차적으로 실행됩니다 (병렬 없음)
2. **타임아웃**: 각 요청은 60초 타임아웃이 있습니다
3. **재시도**: 네트워크 오류 시 1회 재시도합니다
4. **세션 유지**: 모든 요청은 동일한 `session_id`를 재사용합니다
5. **파일 시스템**: 리포트는 `mellow_link/outputs/bench/` 디렉토리에만 저장됩니다

## 문제 해결

### 리포트에 메트릭이 없는 경우

메트릭 수집은 비동기로 처리되므로, 벤치마크 실행 직후에는 일부 메트릭이 아직 DB에 저장되지 않았을 수 있습니다. 몇 초 대기 후 `analyze_mode_benchmark.py`로 다시 분석하거나, DB에서 직접 조회하세요.

### DB 연결 오류

`memory.db` 파일 경로를 확인하세요. 기본 위치:
- `mellow_link/data/memory.db`
- 또는 프로젝트 루트의 `data/memory.db`

## 예제 출력

```
================================================================================
Mode Distribution Benchmark Summary
================================================================================
Timestamp: 2024-02-18T12:00:00
Total Requests: 30
Successful: 30
Failed: 0

--- Mode Distribution ---
  fast: 12 (40.0%)
  thinking: 18 (60.0%)
  unknown: 0 (0.0%)

--- Fallback Statistics ---
  Triggered: 2
  Blocked: 0

--- Performance Metrics ---
  INFER_MS:
    p50: 1250.5ms
    p95: 3200.0ms
    mean: 1450.2ms
  TTFT_MS (measured only):
    p50: 450.0ms
    p95: 1200.0ms
    mean: 520.5ms
  TPS:
    p50: 25.50 tokens/s
    p95: 35.20 tokens/s
    mean: 26.80 tokens/s

--- Top 5 INFER_MS Outliers ---
  1. Index 15: 4500.0ms - 인공지능의 미래에 대해 깊이 있게 분석해주세요
  2. Index 18: 3800.0ms - 양자 컴퓨팅이 암호학에 미치는 영향을 설명해주세요
  ...

--- No Warnings ---
================================================================================
```
