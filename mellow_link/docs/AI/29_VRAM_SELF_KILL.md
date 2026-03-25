# VRAM Self-Kill: 자동 프로세스 종료 및 가비지 컬렉션

## 개요

VRAM 사용량이 95%를 초과할 때, 단순히 에러를 발생시키는 것이 아니라 자동으로 프로세스를 종료(Self-Kill)하고 가비지 컬렉션을 수행하여 시스템을 보호합니다.

## 구현된 기능

### 1. VRAM 체크 및 Self-Kill 메서드 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 1366-1450

**메서드**: `_check_vram_and_kill_if_critical(threshold: float = 95.0) -> Optional[str]`

**동작**:
1. VRAMWatchdog를 사용하여 현재 VRAM 사용량 조회
2. 사용량이 임계값(기본 95%)을 초과하면:
   - 가비지 컬렉션 강제 실행 (2회 수행하여 순환 참조 해제)
   - LLM 컨텍스트 정리
   - "KILLED" 신호 반환
3. 호출자가 "KILLED" 신호를 받으면 즉시 AgentResult 반환하여 루프 종료

**코드**:
```python
async def _check_vram_and_kill_if_critical(self, threshold: float = 95.0) -> Optional[str]:
    """
    [VRAM_SELF_KILL] VRAM 사용량을 체크하고, 임계값(기본 95%)을 초과하면 자동 종료 및 GC 수행.
    """
    # VRAMWatchdog로 현재 사용량 조회
    # 임계값 초과 시 GC 실행 및 "KILLED" 반환
```

### 2. run 메서드 시작 시 VRAM 체크 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 671-690

**동작**:
- `run` 메서드 시작 직후 VRAM 체크 수행
- Self-Kill이 발생하면 즉시 AgentResult 반환하여 루프 진입 전 종료

**코드**:
```python
# ── VRAM_SELF_KILL: VRAM 95% 초과 시 자동 종료 및 GC ──
try:
    vram_status = await self._check_vram_and_kill_if_critical()
    if vram_status == "KILLED":
        return AgentResult(
            answer="[VRAM CRITICAL] VRAM 사용량이 95%를 초과하여 프로세스를 안전하게 종료했습니다.",
            steps=steps,
            total_turns=0,
            finish_reason="vram_critical_self_kill",
            recovery_success=False,
        )
except Exception as e:
    logger.warning(f"[VRAM_SELF_KILL] VRAM 체크 실패 (계속 진행): {e}")
```

### 3. 각 턴 시작 전 VRAM 체크 (`agent_brain.py`)

**위치**: `agent_brain.py` Lines 834-850

**동작**:
- ReAct 루프의 각 턴 시작 전에 VRAM 체크 수행
- Self-Kill이 발생하면 현재 턴에서 즉시 종료

**코드**:
```python
for turn in range(start_turn, effective_max_turns + 1):
    # ── VRAM_SELF_KILL: 각 턴 시작 전 VRAM 체크 ──
    try:
        vram_status = await self._check_vram_and_kill_if_critical()
        if vram_status == "KILLED":
            logger.critical(f"[VRAM_SELF_KILL] Turn {turn}에서 Self-Kill 발생. 루프 종료.")
            return AgentResult(
                answer=f"[VRAM CRITICAL] Turn {turn}에서 VRAM 사용량이 95%를 초과하여 프로세스를 안전하게 종료했습니다.",
                steps=steps,
                total_turns=turn - 1,
                finish_reason="vram_critical_self_kill",
                recovery_success=False,
            )
    except Exception as e:
        logger.warning(f"[VRAM_SELF_KILL] Turn {turn} VRAM 체크 실패 (계속 진행): {e}")
```

## 가비지 컬렉션 전략

### 1. 이중 GC 실행
- 첫 번째 GC: 일반 객체 해제
- 두 번째 GC: 순환 참조 해제 (더 철저한 정리)

### 2. LLM 컨텍스트 정리
- `_llm._contexts` 딕셔너리의 모든 컨텍스트 클리어
- 메모리 사용량 추가 감소

## 효과

1. **시스템 보호**: VRAM 부족으로 인한 시스템 크래시 방지
2. **자동 복구**: GC를 통한 메모리 해제로 시스템 안정성 향상
3. **명확한 종료**: Self-Kill 발생 시 명확한 에러 메시지와 함께 안전하게 종료
4. **실시간 모니터링**: 각 턴마다 VRAM 체크로 조기 감지

## 체크 지점

1. **run 메서드 시작 시**: 루프 진입 전 VRAM 상태 확인
2. **각 턴 시작 전**: ReAct 루프의 각 반복마다 VRAM 체크

## 로깅

- **CRITICAL**: VRAM 임계값 초과 및 Self-Kill 발생
- **WARNING**: VRAM 사용량이 높음 (90% 이상)
- **INFO**: GC 실행 결과 및 컨텍스트 정리 완료
- **DEBUG**: VRAM 체크 실패 또는 스킵 사유

## 주의사항

- VRAMWatchdog가 사용 불가능한 경우 자동으로 스킵 (에러 없이 계속 진행)
- GPU가 없는 환경에서는 체크가 스킵됨
- Self-Kill 발생 시 `finish_reason="vram_critical_self_kill"`로 명확히 표시

## 향후 개선 사항

1. VRAM 사용량에 따른 동적 임계값 조정
2. 이미지 생성 전 예방적 GC 실행
3. VRAM 사용량 히스토리 추적 및 분석
