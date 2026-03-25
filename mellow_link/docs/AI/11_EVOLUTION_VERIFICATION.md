# 🏛️ Mellow-Link 삼권분립 자가발전 검증 체크리스트

**Mellow_Link_Spec.md 및 지시서 기준 구현 상태**

## 1. Backend: 핵심 로직 및 보안

| 항목 | 상태 | 비고 |
|------|------|------|
| ProviderFactory: TOWER/VERDICT/AUDIT 동적 매핑 | ✅ | config/settings.py + core/provider_factory.py |
| Tower (Gemini 2.5 Pro/Flash) 지원 | ✅ | google-generativeai 호출 |
| run_evolution_cycle(): Tower→Verdict→Audit 순차 실행 | ✅ | core/evolution_manager.py |
| 결과 저장: logs/evolution_proposals/{id}.json | ✅ | _save_proposal_to_ledger() |
| apply_from_proposal(): Sandbox 검증 후 파일 수술 | ✅ | _resolve_target() → services/, custom_tools/ |
| NotificationService: 결재 대기 알림 | ✅ | notify_evolution_proposal_ready() |
| NotificationService: 결재 완료 알림 | ✅ | notify_evolution_applied() |
| evolution.log 보안 예외 기록 | ✅ | _log_security_alert() |

## 2. API: 어드민 전용 엔드포인트

| 항목 | 상태 | 비고 |
|------|------|------|
| /evolution 라우터 + get_admin_user_required | ✅ | routers/evolution.py |
| POST /evolution/cycle | ✅ | 자가발전 파이프라인 시작 |
| POST /evolution/apply-from-proposal | ✅ | 승인 후 코드 반영 |
| GET /auth/me + is_admin 필드 | ✅ | user.role == ADMIN |

## 3. Frontend: UI/UX 및 결재 시스템

| 항목 | 상태 | 비고 |
|------|------|------|
| state.js: isAdmin 전역 변수 | ✅ | 로그인 시 갱신 |
| ui-render.js: renderEvolutionReport(data) | ✅ | 관제/판결/검수 카드 |
| 승인 버튼: isAdmin && audit_approved 시에만 활성화 | ✅ | evolution-approve-btn |
| 승인 클릭 → /evolution/apply-from-proposal | ✅ | applyEvolutionProposal() |
| Mellow-Link: 자가발전 요청 버튼 | ✅ | Admin 전용 |

## 4. 안전 가이드라인

| 항목 | 상태 |
|------|------|
| Sandbox: services/, custom_tools/ 외 경로 차단 | ✅ SecurityError |
| No Overwrite: 승인 버튼 클릭 후에만 수술 | ✅ |
| Logging: evolution.log에 보안 예외 기록 | ✅ |

---

## 첫 번째 자가발전 보고서 제출 절차

1. **서버 기동**
   ```bash
   cd d:\AI_Project\mellow_link
   # 기본 바인딩 127.0.0.1; 외부 접근 필요 시 --host 0.0.0.0 또는 MELLOW_API_HOST=0.0.0.0
   python -m uvicorn main:app --host 0.0.0.0 --port 8000
   ```

2. **Admin 계정 로그인**
   - 브라우저에서 앱 접속 → Login → Admin 권한 계정으로 로그인

3. **자가발전 요청**
   - 사이드바 Mellow-Link 섹션 → **자가발전 요청** 버튼 클릭
   - 수정 요청 입력 (예: `services/notification_service.py에 로깅 추가`)

4. **결재 보고서 확인**
   - 채팅창에 관제/판결/검수 결과 카드 표시
   - Telegram 알림 수신 (ENABLE_MOBILE_NOTIFY=true 시)

5. **승인 (검수 통과 시)**
   - audit_approved === true 이고 isAdmin이면 **승인** 버튼 활성화
   - 클릭 → apply-from-proposal 호출 → 코드 반영
   - 결재 완료 Telegram 알림

---

*톱니바퀴가 맞춰졌다. 이제 첫 보고서를 제출하라.*
