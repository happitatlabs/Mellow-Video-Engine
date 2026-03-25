# Mellow Link 에이전트 기능 마인드맵

> 마인드맵 뷰어(Mermaid 지원) 또는 [Mermaid Live Editor](https://mermaid.live)에서 시각화 가능

```mermaid
mindmap
  root((Mellow Link))
    인증 Auth
      회원가입 /auth/register
      로그인 /auth/token
      게스트 로그인 /auth/guest-login
      현재 사용자 /auth/me
    폴더 Folders
      폴더 목록 /folders
      폴더 생성/수정/삭제
      세션 조회 /folders/:id/sessions
      문서 조회/업로드/삭제
    채팅 Chat
      대화 요청 /chat/ask
      LLM 직접 /chat
      세션 목록/메시지/삭제
      임시 문서 업로드 /chat/upload-temp
    AI 생성
      이미지 /generate-image
      문서 /generate-document
    아바타 Avatar
      상태 /avatar/status
      발화 /avatar/speak
      어드민 아바타 실행
    시스템 System
      헬스체크 /health
      상태 /status
      VRAM 모니터링 /vram
      메트릭 /metrics
    삼권분립 Evolution
      관제 Tower
        Gemini
        로그 분석
        수정 방향 제시
      판결 Verdict
        GPT-4o
        코드/Diff 작성
      검수 Audit
        Claude
        보안 검증
      API
        사이클 실행 /evolution/cycle
        제안서 적용 apply-from-proposal
        로그 조회 /evolution/logs
    자율 에이전트 Autonomous
      Tower 계획
        대기 작업 컨텍스트
        도구 인벤토리
        action create/modify/reuse
      윤리 검토 Guardian
      승인 대기
      Verdict 코드 생성
        workspace 스크립트
        Description 메타데이터
      실행
        AST 검사
        Guardian 정밀검수
        subprocess 실행
      API
        run-tick
        report
        approve/reject
      텔레그램
        승인 대기 알림
        인라인 버튼 승인/거부
        실패 보고
    서비스 Services
      이미지 image_service
      문서 doc_service
      RAG rag_service
      비디오 video_processor
      VTuber 릴레이 vtuber_relay
      알림 notification_service
        텔레그램
        Evolution 결재
        자율 작업
    코어 Core
      evolution_manager
        EvolutionProposal
        dry-run/diff/롤백
      guardian_service
        윤리 검토
        코드 검수
      autonomous_agent
        run_autonomous_tick
        execute_approved_work
      risk_classifier
        Level 1/2/3
      tool_forge
        AST 보안 검사
      workspace_sandbox
        경로 검증
        쓰기 제한
      goal_manager
      scheduler_service
    인프라 Infra
      memory_database
        autonomous_work_results
        evolution_logs
        goals
        api_usage_logs
      provider_factory
        Google/OpenAI/Anthropic
        일일 쿼터
```

---

## 텍스트 트리 (간단 요약)

```
Mellow Link
├── 인증 (Auth): 회원가입, 로그인, 게스트, /auth/me
├── 폴더 (Folders): CRUD, 세션, 문서 업로드
├── 채팅 (Chat): /chat/ask, 세션/메시지 관리, 임시 문서
├── AI 생성: 이미지, 문서
├── 아바타: 상태, 발화, 어드민 실행
├── 시스템: health, status, VRAM, metrics
├── 삼권분립 (Evolution) [어드민]
│   ├── Tower(Gemini) → Verdict(GPT-4o) → Audit(Claude)
│   ├── /evolution/cycle, apply-from-proposal, logs
│   └── 결재 보고서, VIP 텔레그램 알림
├── 자율 에이전트 (Autonomous) [어드민]
│   ├── Tower 계획 (대기 작업, 도구 인벤토리, action)
│   ├── 중복 배팅 차단 (80% 유사도)
│   ├── 윤리 검토 → Verdict 코드 → AST/Guardian 검증
│   ├── workspace/ 스크립트 실행, PYTHONPATH
│   ├── /autonomous/run-tick, report, approve, reject
│   └── 텔레그램 인라인 버튼 승인/거부, 실패 보고
├── 웹훅: /webhooks/telegram (인라인 버튼 callback)
├── 서비스: image, doc, RAG, video, vtuber_relay, notification
├── 코어: evolution_manager, guardian, autonomous_agent, risk_classifier, tool_forge, workspace_sandbox
└── 인프라: memory_database, provider_factory, env_loader
```
