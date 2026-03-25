# VRAM 최적화: 이미지 엔진 호출 전 LLM 컨텍스트 정리

## 개요

이미지/비디오 생성은 VRAM을 많이 사용하므로, 이미지 엔진 호출 전에 불필요한 LLM 컨텍스트를 정리하여 메모리를 확보합니다.

## 구현된 최적화

### 1. 이미지 생성 전 LLM 컨텍스트 공격적 트리밍 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 1006-1025

**동작**:
- `create_image` 또는 `animate_image` 도구 호출 전에 자동으로 실행
- 히스토리를 시스템 프롬프트 + 최근 3턴(6개 메시지)만 유지하도록 축소
- LLM 서비스의 컨텍스트도 최근 3개 메시지만 유지하도록 정리

**코드**:
```python
# ── VRAM_OPTIMIZATION: 이미지 생성 전 LLM 컨텍스트 정리 ──
if action.tool in ("create_image", "animate_image"):
    # 히스토리를 더 공격적으로 트리밍 (시스템 프롬프트 + 최근 3턴만 유지)
    if len(messages) > 4:
        system_msg = messages[0]
        recent_messages = messages[-6:] if len(messages) > 6 else messages[1:]
        messages = [system_msg] + recent_messages
        logger.info(f"[VRAM_OPTIMIZATION] 이미지 생성 전 LLM 컨텍스트 정리: {len(messages)}개 메시지로 축소")
    
    # LLM 서비스의 컨텍스트도 정리 (가능한 경우)
    try:
        if hasattr(self._llm, '_contexts') and isinstance(self._llm._contexts, dict):
            for context_id, context in self._llm._contexts.items():
                if hasattr(context, 'messages') and len(context.messages) > 3:
                    context.messages = context.messages[-3:]
                    logger.debug(f"[VRAM_OPTIMIZATION] LLM 컨텍스트 '{context_id}' 정리 완료")
    except Exception as e:
        logger.debug(f"[VRAM_OPTIMIZATION] LLM 컨텍스트 정리 실패 (무시): {e}")
```

### 2. 히스토리 트리밍 메서드 개선 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 1308-1330

**변경 사항**:
- `_trim_history` 메서드에 `aggressive` 파라미터 추가
- 공격적 모드에서는 최근 3턴(6개 메시지)만 유지
- 이미지 생성 직후에도 공격적 트리밍 적용

**코드**:
```python
def _trim_history(self, messages: List[Dict[str, str]], aggressive: bool = False) -> List[Dict[str, str]]:
    """
    컨텍스트 윈도우 초과 시 오래된 메시지를 잘라냄.
    시스템 프롬프트(첫 메시지)는 항상 유지.
    
    Args:
        messages: 메시지 리스트
        aggressive: True이면 더 공격적으로 트리밍 (VRAM 절약용, 이미지 생성 시 사용)
    """
    if len(messages) <= self._context_window + 1 and not aggressive:
        return messages

    system_msg = messages[0]
    
    if aggressive:
        # 공격적 트리밍: 최근 3턴만 유지 (시스템 프롬프트 + 최근 6개 메시지)
        if len(messages) > 7:
            recent = messages[-6:]
            return [system_msg] + recent
        return messages
    
    recent = messages[-(self._context_window):]
    return [system_msg] + recent
```

### 3. 이미지 생성 후 히스토리 트리밍 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 1153-1157

**동작**:
- 이미지 생성 직후 Observation을 히스토리에 추가한 뒤, 공격적 트리밍 적용
- 이미지 생성 전후 모두에서 컨텍스트를 최소화하여 VRAM 절약

**코드**:
```python
# 히스토리 트리밍 (시스템 프롬프트는 유지)
# 이미지 생성 직후에는 공격적 트리밍 적용 (VRAM 절약)
is_after_image_generation = (
    action and action.tool in ("create_image", "animate_image")
)
messages = self._trim_history(messages, aggressive=is_after_image_generation)
```

### 4. 이미지 생성 도구 함수 로깅 (`agent_tools.py`)

**위치**: `agent_tools.py` Lines 1036-1038, 1089-1091

**동작**:
- 이미지/비디오 생성 시작 시 VRAM 최적화 적용 로그 기록
- 디버깅 및 모니터링 용이

**코드**:
```python
# [VRAM_OPTIMIZATION] 이미지 생성 전 VRAM 상태 로깅
logger.info("[create_image] 이미지 생성 시작 (VRAM 최적화 적용됨)")

# [VRAM_OPTIMIZATION] 비디오 생성 전 VRAM 상태 로깅
logger.info("[animate_image] 비디오 생성 시작 (VRAM 최적화 적용됨)")
```

## 효과

1. **VRAM 절약**: 이미지 생성 전에 LLM 컨텍스트를 최소화하여 메모리 확보
2. **자동 최적화**: 이미지 생성 도구 호출 시 자동으로 컨텍스트 정리
3. **이중 보호**: 이미지 생성 전후 모두에서 컨텍스트 최소화
4. **안전성**: 시스템 프롬프트는 항상 유지하여 에이전트 기능 보장

## 적용 범위

- `create_image`: 이미지 생성 도구
- `animate_image`: 비디오 생성 도구 (SVD)

## 주의사항

- 시스템 프롬프트는 항상 유지되므로 에이전트의 기본 동작은 보장됨
- 최근 3턴만 유지하므로 장기 컨텍스트가 필요한 작업에는 영향이 있을 수 있음
- 이미지 생성이 아닌 일반 작업에서는 기존 `_context_window` 설정이 적용됨

## 향후 개선 사항

1. VRAM 사용량 모니터링을 통한 동적 컨텍스트 크기 조정
2. 이미지 생성 완료 후 컨텍스트 복구 옵션
3. 사용자 설정을 통한 공격적 트리밍 임계값 조정
