# PolicyGuardian 스펙 (정책 게이트)

운영자/감사자용 정의 문서.  
**"왜 거부됐지?"** → 이 문서의 결정 규칙·risk_level·risk_score·critique 템플릿으로 확인.

---

## 1. Guardian 모드 선택

| 환경       | 구현체           | 조건                     |
|------------|------------------|--------------------------|
| 폐쇄망 기본 | **PolicyGuardian** | `ENABLE_GUARDIAN_APIS=0` (또는 미설정) |
| 연결망     | **AIGuardian**     | `ENABLE_GUARDIAN_APIS=1` |

- Factory `get_guardian_service()`가 위 조건에 따라 **한 번** 구현체를 선택한다.
- **싱글톤 동작**: 선택된 구현체는 **프로세스 수명 동안 유지**된다.  
  런타임에 env를 바꿔도 반영되지 않으며, **재시작이 필요**하다.  
  납품 환경에서는 재시작 전까지 일관된 정책이 유지되어 오동작을 줄인다.

---

## 2. PolicyGuardian 한정: risk_level (코드 위험도)

`risk_classifier.classify_code_risk_level(code)` 결과.

| risk_level | 의미           | 대표 패턴 예시 |
|------------|----------------|----------------|
| **1**      | 단순 조회/계산 | read, listdir, iterdir, print 등. L2/L3 패턴 없음 |
| **2**      | 파일 쓰기/수정 | open(..., 'w'/'a'), write_text, shutil.copy, os.remove 등 |
| **3**      | 네트워크/시스템 | subprocess, eval/exec, requests, socket, shell=True 등 |

---

## 3. PolicyGuardian 한정: risk_score (0–100)

| risk_score | 의미 |
|------------|------|
| **0**  | 안전. 자동 승인 가능. |
| **50** | 보류. Operator 또는 AIGuardian 승인 필요 (NEED_AI_REVIEW). |
| **100**| 거부. 자동 적용 불가. |

---

## 4. PolicyGuardian 한정: 정책 결정(enum)

| 결정 | 의미 | is_approved | 비고 |
|------|------|------------|------|
| **APPROVE**       | 규칙 기반 통과. 자동 승인. | True  | L1 코드 등 |
| **NEED_AI_REVIEW**| 보류. Operator/AI 심사 필요. | False | L2 코드. 폐쇄망에서는 Operator 승인 후 진행. |
| **REJECT**        | 거부. 자동 적용 불가. | False | L3, 파일범위 위반 등 |

---

## 5. 결정 규칙 요약 (코드/진화 제안)

| 조건 | policy_decision | risk_score | is_approved |
|------|------------------|------------|-------------|
| 파일범위 밖 (project_root 외) | REJECT | 0 | False |
| risk_level == 3 | REJECT | 100 | False |
| risk_level == 2 | **NEED_AI_REVIEW** | 50 | False |
| risk_level == 1 | APPROVE | 0 | True |

- **audit_insight**: PolicyGuardian은 LLM 미호출이므로 항상 APPROVE(로컬 분석 신뢰).
- **audit_autonomous_ethics**: PolicyGuardian은 항상 (False, "AIGuardian 필요", "폐쇄망"). 윤리 검수는 연결망+AIGuardian 필요.

---

## 6. Critique 템플릿 (PolicyGuardian)

| 결정 | critique 예시 |
|------|-------------------------------|
| APPROVE (insight) | `PolicyGuardian(ENABLE_GUARDIAN_APIS=0): 규칙 기반만 사용. LLM 호출 없음. 로컬 분석 신뢰.` |
| APPROVE (L1 코드) | `PolicyGuardian: 규칙 기반 검수 통과 (level=1). LLM 검수 없음.` |
| NEED_AI_REVIEW (L2) | `PolicyGuardian: NEED_AI_REVIEW (level=2). Operator 또는 AIGuardian 승인 필요.` |
| REJECT (L3) | `PolicyGuardian: REJECT. Level 3 위험 패턴. ({reason})` |
| REJECT (파일범위) | `PolicyGuardian: 대상 파일이 허용 범위 밖: {path}` |

---

## 7. AIGuardian과의 차이

- **AIGuardian**: `policy_decision`을 설정하지 않음(None). LLM이 is_approved/risk_score 반환.
- **PolicyGuardian**: 모든 AuditResult에 `policy_decision` 설정. LLM 호출 없음.

하위 시스템(EvolutionManager, ToolForge 등)은 `policy_decision == "NEED_AI_REVIEW"`일 때 "Operator 승인 대기" 등 보류 플로우를 붙일 수 있다.
