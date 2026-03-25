# Long-form Output Policy (Summary-First) 패치

## 개요

THINKING 모드에서 장문 질문에 대한 응답을 요약 우선으로 제한하여 p95 지연 시간을 감소시키는 패치입니다.

## 목표

- 장문 질문에 대해 기본적으로 요약만 제공 (10~15줄, 800자 이내)
- 사용자가 명시적으로 "확장"을 요청할 때만 전체 답변 제공
- THINKING/RESEARCH 모드에만 적용 (FAST 모드 제외)
- 도구 라우팅 동작에는 영향 없음

## 구현 위치

### 1. 장문 요청 감지 (`agent_prompts.py`)

**함수**: `_is_long_form_request(user_input: str, threshold: Optional[int] = None) -> bool`

**감지 방법**:
- 키워드 기반 (한글): "분석", "비교", "탐구", "정리", "설명", "리포트", "전망", "전략", "계획" 등
- 키워드 기반 (영어): "analysis", "compare", "explain", "investigate", "report", "strategy", "plan" 등
- 길이 기반: `len(user_input) >= threshold` (기본값: 30자, 환경변수로 설정 가능)

### 2. 확장 모드 감지 (`agent_prompts.py`)

**함수**: `_is_expansion_request(user_input: str) -> bool` (별칭: `is_expand_request`)

**감지 방법**:
- **한글 키워드**: "확장", "더 자세히", "자세히", "상세히", "계속", "전체", "풀버전", "더 보여", "완전한", "전체 답변", "상세 설명", "더 설명", "더 알려"
- **영어 키워드**: "expand", "more detail", "more details", "detailed", "continue", "full", "show more", "complete", "full version", "full answer", "full response", "full explanation", "tell me more", "more info", "more information"
- **정규식 패턴**: "확장해줘", "자세히 설명해줘", "더 자세히 알려줘", "expand please", "more detail", "show more" 등

**로깅**:
- 확장 요청 감지 시 `[OUTPUT_POLICY] expanded_mode=True` 로그 출력

### 3. OUTPUT_POLICY 블록 주입 (`agent_prompts.py`)

**블록 내용**:
```
[OUTPUT_POLICY]
장문 질문에 대한 응답은 반드시 다음 구조로 작성하세요:

[요약 개요]
- 주제 한 줄 정의

[핵심 포인트]
1) 핵심 주장 또는 개념 A
   - 왜 중요한지 한 줄
2) 핵심 주장 또는 개념 B
   - 맥락 또는 조건 한 줄
3) 핵심 주장 또는 개념 C
   - 반론 또는 한계 한 줄

[구조적 정리]
- 원인 → 결과 또는
- 전제 → 논리 → 결론
(2~3줄 이내)

[결론]
- 한 문장 요약
- 현실적 의미 또는 적용 가능성 한 줄

---
더 자세히 보려면 "확장"이라고 입력하세요.

규칙:
- 800자 이내로 제한
- 각 섹션은 간결하게 작성
- 핵심 정보만 포함
- 장문 설명 금지
```

**주입 조건**:
- 모드가 "thinking" 또는 "research"
- 장문 요청 감지됨 (`_is_long_form_request` == True)
- 확장 모드가 아님 (`force_expanded` == False)

### 4. Agent Brain 통합 (`agent_brain.py`)

**변경 사항**:
- `build_system_prompt` 호출 시 `user_input`과 `force_expanded` 파라미터 전달
- 확장 요청 감지 후 `force_expanded=True`로 설정
- FAST 모드에서 THINKING 모드로 에스컬레이션 시에도 확장 모드 감지 적용

## 환경변수 설정

`.env` 파일에 다음 설정 추가:

```env
# 장문 요청 감지 임계값 (문자 수, 기본값: 30)
MELLOW_LONG_FORM_THRESHOLD=30

# OUTPUT_POLICY 활성화 여부 (기본값: true)
MELLOW_ENABLE_OUTPUT_POLICY=true
```

## 사용 예시

### 일반 장문 질문 (요약 모드)

**사용자 입력**: "이 프로젝트의 아키텍처를 분석해줘"

**응답**: 
- 10~15줄 요약 (800자 이내)
- 끝에 "더 자세히 보려면 '확장'이라고 입력하세요" 메시지 포함

### 확장 요청 (전체 답변)

**사용자 입력**: "확장해줘" 또는 "자세히 설명해줘"

**응답**: 
- OUTPUT_POLICY 적용 안 함
- 전체 답변 제공

## 테스트

테스트 파일: `tests/test_long_form_policy.py`

실행 방법:
```bash
cd d:\AI_Project
python -m mellow_link.tests.test_long_form_policy
```

테스트 항목:
- 한글/영어 키워드 기반 장문 감지
- 길이 기반 장문 감지
- 확장 요청 감지
- OUTPUT_POLICY 블록 내용 확인
- 환경변수 임계값 테스트

## 제약사항

1. **FAST 모드 제외**: FAST 모드에서는 OUTPUT_POLICY가 적용되지 않음
2. **도구 라우팅 보존**: 도구 호출이 필요한 경우 정상적으로 동작
3. **최소한의 패치**: 기존 아키텍처를 크게 변경하지 않음

## 코드 변경 요약

### 수정된 파일

1. **`mellow_link/core/agent_prompts.py`**
   - `_is_long_form_request()` 함수 추가
   - `_is_expansion_request()` 함수 추가
   - `OUTPUT_POLICY_BLOCK` 상수 추가
   - `build_system_prompt()` 함수에 `user_input`, `force_expanded` 파라미터 추가
   - `build_system_prompt_assembled()` 함수에 OUTPUT_POLICY 주입 로직 추가

2. **`mellow_link/core/agent_brain.py`**
   - `_is_expansion_request` import 추가
   - `build_system_prompt()` 호출 시 `user_input`, `force_expanded` 전달
   - 에스컬레이션 시에도 확장 모드 감지 적용

3. **`mellow_link/.env`**
   - `MELLOW_LONG_FORM_THRESHOLD` 환경변수 추가
   - `MELLOW_ENABLE_OUTPUT_POLICY` 환경변수 추가

### 새로 생성된 파일

1. **`mellow_link/tests/test_long_form_policy.py`**
   - 장문 감지 및 확장 모드 감지 테스트 코드

2. **`mellow_link/docs/LONG_FORM_OUTPUT_POLICY.md`**
   - 이 문서

## 성능 영향

- **예상 효과**: THINKING 모드에서 장문 질문의 p95 지연 시간 감소
- **오버헤드**: 장문 감지 로직은 매우 가벼움 (키워드 매칭 + 길이 체크)
- **메모리**: OUTPUT_POLICY 블록은 약 200자 정도의 추가 프롬프트

## 향후 개선 사항

1. 사용자 피드백 기반 요약 품질 개선
2. 도메인별 키워드 확장 (예: 코딩 관련 키워드)
3. 요약 길이 동적 조정 (질문 복잡도에 따라)
4. 요약 품질 메트릭 수집 및 모니터링
