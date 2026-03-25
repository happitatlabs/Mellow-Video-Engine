# TTFT Performance Fix

## 변경 사항 요약

TTFT (Time To First Token) 성능 저하 문제를 해결하기 위한 최적화:

1. **디버그 로깅 추가**: 각 요청 전 LLM 호출 정보 로깅
2. **Docs Auto Injection 제한**: fast 모드에서 비활성화, thinking/research 모드에서만 활성화
3. **Docs 내용 제한**: 자동 주입 시 최대 1000자로 제한
4. **Feature Flag 추가**: `DOCS_AUTO_ENABLED`로 전체 기능 비활성화 가능

## 변경된 파일

- `mellow_link/config/settings.py`: `docs_auto_enabled` 설정 추가
- `mellow_link/core/agent_docs_auto.py`: 모드 제한 및 내용 제한 추가
- `mellow_link/core/agent_brain.py`: 디버그 로깅 및 모드 전달 추가

## Feature Flag 사용법

### 환경 변수로 설정

`.env` 파일에 추가 (`mellow_link/.env`):

```bash
# Docs auto injection 활성화/비활성화 (기본값: 1 = 활성화)
DOCS_AUTO_ENABLED=1  # 또는 0으로 비활성화
```

또는 환경 변수로:

```bash
export DOCS_AUTO_ENABLED=0  # 비활성화
```

**참고**: `DOCS_AUTO_ENABLED=0`으로 설정하면 `try_auto_read_docs`가 완전히 건너뛰어집니다.

### 설정 확인

서버 시작 시 로그에서 확인하거나, Python에서:

```python
from mellow_link.config import get_settings
settings = get_settings()
print(f"Docs auto enabled: {settings.docs_auto_enabled}")
```

## 디버그 로깅

각 요청의 첫 번째 턴에서 다음 정보가 로깅됩니다:

```
[TTFT_DEBUG] effective_mode=fast, model=qwen2.5:7b, num_ctx=8192, prompt_chars=1234, estimated_tokens=308, docs_auto_injected=False
```

- `effective_mode`: 실제 사용된 모드 (fast/thinking/research)
- `model`: 선택된 모델 이름
- `num_ctx`: 컨텍스트 크기 (가능한 경우)
- `prompt_chars`: 프롬프트 문자 수
- `estimated_tokens`: 추정 토큰 수 (chars / 4)
- `docs_auto_injected`: docs 자동 주입 여부

## Docs Auto Injection 제한

### 모드 제한

- **fast 모드**: docs auto injection 비활성화 (TTFT 최적화)
- **thinking/research 모드**: docs auto injection 활성화

### 내용 제한

자동 주입 시:
- 최대 1000자로 제한
- 초과 시 `[TRUNCATED]` 마커 추가

## 벤치마크 재실행

### 1. Feature Flag 비활성화 테스트

```bash
# .env 파일에 추가
DOCS_AUTO_ENABLED=0

# 서버 재시작
python main.py
```

### 2. 벤치마크 실행

```bash
# 프로젝트 루트에서
python mellow_link/scripts/run_mode_benchmark.py
```

### 3. 결과 비교

- **이전**: TTFT가 느린 경우 docs auto injection이 원인일 수 있음
- **이후**: fast 모드에서 docs auto injection 비활성화로 TTFT 개선

### 4. 로그 확인

서버 로그에서 `[TTFT_DEBUG]` 라인 확인:

```bash
# 로그에서 TTFT_DEBUG 필터링
grep "TTFT_DEBUG" logs/app.log

# 또는 실시간 확인
tail -f logs/app.log | grep "TTFT_DEBUG"
```

## 성능 개선 예상 효과

1. **Fast 모드**: docs auto injection 비활성화로 TTFT 감소
2. **Thinking/Research 모드**: 내용 제한(1000자)으로 프롬프트 크기 감소 → TTFT 개선
3. **Feature Flag**: 필요 시 전체 기능 비활성화 가능

## 추가 최적화 가능성

만약 여전히 느리다면:

1. 로그에서 `docs_auto_injected=True`인 요청 확인
2. `prompt_chars`가 큰 요청 확인
3. `num_ctx` 설정 확인 (너무 크면 성능 저하 가능)
