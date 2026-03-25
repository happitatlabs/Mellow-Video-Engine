# VRAM 관리: 상태 전환 시 모델 언로드

## 문제점

VRAM을 계속 많이 사용하는 주요 원인은 **Orchestrator가 상태 전환 시 모델을 언로드하지 않기 때문**입니다.

### 기존 문제
- IMAGE 상태에서 TEXT 상태로 전환해도 이미지 모델(ComfyUI)이 VRAM에 남아있음
- TEXT 상태에서 IMAGE 상태로 전환해도 LLM 모델(Ollama)이 VRAM에 남아있음
- 두 모델이 동시에 VRAM에 로드되어 메모리 부족 발생

## 해결 방법

### 상태 전환 시 자동 모델 언로드 (`orchestrator.py`)

**위치**: `orchestrator.py` Lines 481-503

**구현**:
- `request_state_change` 메서드에서 상태 전환 전에 이전 상태의 모델을 언로드
- IMAGE -> TEXT/IDLE: ImageService의 `unload_model()` 호출
- TEXT -> IMAGE/IDLE: LLMService의 `unload_model()` 호출
- 모델 언로드 후 가비지 컬렉션 강제 실행

**코드**:
```python
# ── VRAM_MANAGEMENT: 상태 전환 시 이전 상태의 모델 언로드 ──
# IMAGE -> TEXT/IDLE: 이미지 모델 언로드
if previous_state == SystemState.IMAGE and target_state != SystemState.IMAGE:
    image_service = self._services.get("image")
    if image_service and hasattr(image_service, "unload_model"):
        unload_success = await image_service.unload_model()
        if unload_success:
            logger.info("[Orchestrator] 이미지 모델 언로드 완료 (VRAM 해제)")

# TEXT -> IMAGE/IDLE: LLM 모델 언로드
if previous_state == SystemState.TEXT and target_state != SystemState.TEXT:
    llm_service = self._services.get("llm")
    if llm_service and hasattr(llm_service, "unload_model"):
        unload_success = await llm_service.unload_model()
        if unload_success:
            logger.info("[Orchestrator] LLM 모델 언로드 완료 (VRAM 해제)")

# 가비지 컬렉션 강제 실행
if previous_state != target_state and previous_state != SystemState.IDLE:
    import gc
    collected = gc.collect()
    logger.debug(f"[Orchestrator] 상태 전환 후 GC 실행: {collected}개 객체 해제")
```

## 효과

1. **VRAM 절약**: 상태 전환 시 이전 모델이 자동으로 언로드되어 메모리 해제
2. **자동 관리**: 수동으로 모델 언로드를 호출할 필요 없음
3. **안정성 향상**: VRAM 부족으로 인한 오류 감소
4. **명확한 로깅**: 언로드 성공/실패를 로그로 확인 가능

## 상태 전환 시나리오

### 시나리오 1: 이미지 생성 → 채팅
1. IMAGE 상태에서 이미지 생성 완료
2. TEXT 상태로 전환
3. **자동으로 이미지 모델 언로드** (VRAM 해제)
4. LLM 모델 로드 (필요 시)

### 시나리오 2: 채팅 → 이미지 생성
1. TEXT 상태에서 채팅 완료
2. IMAGE 상태로 전환
3. **자동으로 LLM 모델 언로드** (VRAM 해제)
4. 이미지 모델 로드 (필요 시)

### 시나리오 3: 작업 완료 → IDLE
1. TEXT 또는 IMAGE 상태에서 작업 완료
2. IDLE 상태로 전환
3. **자동으로 해당 모델 언로드** (VRAM 해제)

## 주의사항

- 모델 언로드는 비동기 작업이므로 시간이 걸릴 수 있음
- 언로드 실패 시 경고 로그가 기록되지만 상태 전환은 계속 진행됨
- IDLE 상태에서는 모델이 언로드되어 있어야 함 (필요 시 자동 로드)

## 향후 개선 사항

1. 언로드 타임아웃 설정
2. 언로드 실패 시 재시도 로직
3. VRAM 사용량 모니터링을 통한 동적 언로드 결정
