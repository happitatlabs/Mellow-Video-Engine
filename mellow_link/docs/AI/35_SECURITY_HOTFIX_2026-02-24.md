# 보안 핫픽 (2026-02-24)

무인증 접근·IDOR·기본 비밀번호·외부 포트 노출 차단 적용.

## 1. [Critical] Chat IDOR 차단

- **위치**: `routers/chat.py` — `/chat/ask`
- **조치**:
  - **미인증 사용자가 `session_id`를 보내면 403** 반환. (기존: session_id만으로 타인 세션 조회 가능)
  - 로그인 사용자는 `user_id`로만 세션 조회(소유권 검증 유지).
- **메시지**: `"세션을 사용하려면 로그인이 필요합니다. (무인증 session_id 접근 차단)"`

## 2. [High] runs/* API 인증·소유권·Admin 전용

- **위치**: `routers/runs.py`
- **조치**:
  - **모든 runs 엔드포인트**: `Depends(get_current_user)` — **로그인 필수**.
  - **Run 소유권**: `run.session_id`가 해당 사용자의 채팅 세션일 때만 허용. (본인 run만 조회/시작/이벤트 스트림)
  - **운영자 제어 API (Admin 전용)**  
    `Depends(get_admin_user_required)` 적용:
    - `POST /runs/{run_id}/control` (pause / retry / abort / force_finish)
    - `POST /runs/{run_id}/mode` (force_fast 등)
    - `POST /runs/{run_id}/propose-tool`
- **헬퍼**: `_user_session_id_set`, `_run_owned_by_user`, `_get_run_or_404`

## 3. [High] 기본 관리자 비밀번호 제거

- **위치**: `core/security.py`
- **조치**:
  - **기본 비밀번호 제거**: `ADMIN_PASSWORD` 환경 변수 미설정 시 빈 문자열 사용. (기존 `mellow1234` 제거)
  - **초기 관리자 생성**: 관리자가 한 명도 없고 `ADMIN_PASSWORD`가 비어 있으면 **부트스트랩 시 `RuntimeError` 발생** → 서버 기동 실패.
  - 초기 관리자를 만들려면 **반드시 `.env` 또는 환경 변수에 `ADMIN_PASSWORD` 설정 후 재시작**.

## 4. 외부 노출 포트 제한

- **위치**: `config/settings.py`, `main.py`
- **조치**:
  - **기본 바인딩**: `api_host` / `server_host` 기본값을 `0.0.0.0` → **`127.0.0.1`** 로 변경.
  - 외부에서 접근하려면 **`MELLOW_API_HOST=0.0.0.0`** (또는 `SERVER_HOST=0.0.0.0`) 설정 필요.
  - `main.py` docstring: uvicorn 예시를 `--host 127.0.0.1`로 수정.

---

## 보안 하드닝 (토큰/에러/웹훅)

### 4. [Medium] 토큰 Query String 제거

- **dependencies.py**: `get_admin_user_for_flow_view`, `resolve_console_viewer`에서 **`access_token` 쿼리 파라미터 제거**. **Authorization 헤더만** 사용.
- **static (user_console, operator_console, flow_monitor)**: URL에 `access_token`을 붙이거나 읽지 않음. 링크는 `run_id` 등만 쿼리로 전달, 인증은 localStorage + 헤더만 사용.

### 5. [Medium] 전역 예외 핸들러 상세 비노출

- **main.py**: `global_exception_handler`에서 **응답에는 `detail: "서버 내부 오류가 발생했습니다."` 만 반환**. `str(exc)`/스택은 **서버 로그 전용** (`logger.error(..., exc_info=True)`).

### 6. [Medium] Telegram Webhook 서명 검증

- **config/settings.py**: `telegram_webhook_secret` (환경 변수 `TELEGRAM_WEBHOOK_SECRET`) 추가.
- **routers/telegram.py**: `TELEGRAM_WEBHOOK_SECRET`이 설정된 경우 **`X-Telegram-Bot-Api-Secret-Token` 헤더가 값과 일치해야만** 웹훅 처리. 불일치 시 403.
- **설정 방법**: BotFather로 웹훅 설정 시 secret_token 지정 후, 동일 값을 `.env`에 `TELEGRAM_WEBHOOK_SECRET=...` 로 설정.

---

## 보안 회귀 테스트 (인증/인가 E2E)

- **파일**: `mellow_link/tests/test_auth_e2e.py`
- **시나리오**:
  - **IDOR**: 미인증·잘못된 토큰으로 `session_id` 포함 POST `/chat/ask` → 403
  - **무인증 run**: GET/POST `/runs`, `/runs/{id}`, `/runs/{id}/events`, `/runs/{id}/start` 인증 없이 호출 → 401
  - **Run control**: 인증 없이 또는 guest 토큰으로 `/runs/{id}/control` → 401 또는 403
- **실행** (의존성 설치 후):
  ```bash
  pytest -q mellow_link/tests/test_security_manager.py mellow_link/tests/test_auth_e2e.py
  ```
- 앱 로드 실패 시(psutil 등 의존성 부족) 테스트는 스킵됨.

---

## 2차 재검수 반영

- **runs 소유권**: `session_id`가 None/빈 run은 **소유자 불명**으로 간주 → `_run_owned_by_user` False, 목록에서 제외. 세션 미연결 run이 타 사용자에게 노출되지 않음.
- **JWT 운영 필수화**: `MELLOW_ENV=production` 또는 `MELLOW_REQUIRE_JWT_SECRET=1` 이면 **JWT_SECRET_KEY/MELLOW_JWT_SECRET** 미설정 시 기동 실패 (`infra/database.py`).

---

## 배포 시 참고

- **최초 설치**: DB에 관리자가 없으면 `ADMIN_PASSWORD`를 반드시 설정해야 서버가 기동합니다.
- **기존 설치**: 이미 관리자 계정이 있으면 `ADMIN_PASSWORD` 미설정도 기동 가능 (부트스트랩은 스킵).
- **외부 접근**: 리버스 프록시(Nginx 등) 뒤에서만 노출하거나, 필요 시에만 `MELLOW_API_HOST=0.0.0.0` 사용 권장.
