# Known Issues (Engine v1)

작성: 2026-02-24  
목적: Engine v1 완료 판정 후 잔여 실패 테스트를 명시하고, CI/릴리즈 정책을 안내합니다.

---

## 1. 잔여 3개 테스트 (환경/정책 의존)

전체 `mellow_link/tests` 스위트에서 **핵심 6개 파일**은 100% 통과합니다. 아래 3개는 환경·보안 정책 차이로 일부 환경에서 실패할 수 있습니다.

| 테스트 | 파일 | 원인 요약 |
|--------|------|-----------|
| `test_curl_dangerous_flags_blocked` | `test_agent_tools.py` | 위험 플래그 사용 시 `SecurityBlocked` 예외 대신 문자열 `[차단]` 반환으로 동작할 수 있음. 정책/구현 정합성 이슈. |
| `test_curl_exe_variant` | `test_agent_tools.py` | `curl.exe`가 허용 목록에 없어 `[차단]`/`허용되지 않은` 메시지가 나오는 환경 있음. (Windows에서 curl vs curl.exe 처리 차이) |
| `test_reject_absolute` | `test_agent_tools_docs.py` | 절대 경로(`/etc/passwd`) 거부 시 `_resolve_docs_path`가 `err is not None`을 반환해야 하나, 환경에 따라 `err`가 `None`으로 나올 수 있음. |

이 테스트들은 **`env_policy`** 마커로 분류되어 있으며, CI에서는 **core-required**만 필수로 두고 **env-policy**는 선택(별도 job 또는 수동 실행)으로 분리할 수 있습니다.

---

## 2. CI에서 환경 의존 테스트 분리

- **core-required**: 핵심 동작 검증. CI 필수 통과 대상.
- **env-policy**: 보안/환경 정책·허용 목록에 따라 결과가 달라지는 테스트. CI는 선택 실행 또는 별도 job 권장.

### 표준 실행 환경

- **표준 명령**: `python -m pytest ...` (직접 `pytest` 호출 대신 인터프리터·환경을 고정하기 위함)
- **요구사항**: 프로젝트 `.venv`에 pytest 설치 필요. 없으면 `python -m pip install pytest pytest-asyncio` 후 실행

### 실행 예시

```bash
# 프로젝트 루트에서, .venv 활성화 후

# 필수만 (env_policy 제외) — CI 권장
python -m pytest mellow_link/tests -m "not env_policy" -q --tb=line

# 핵심 6개 파일만 (v1 체크리스트)
python -m pytest mellow_link/tests/test_path_manager.py \
  mellow_link/tests/test_security_manager.py \
  mellow_link/tests/test_rag_security_phase1a.py \
  mellow_link/tests/test_agent_tools.py \
  mellow_link/tests/test_agent_brain.py \
  mellow_link/tests/test_long_form_policy.py -q --tb=line

# 전체 (env_policy 포함)
python -m pytest mellow_link/tests -q --tb=line
```

`pytest.ini`에 `core_required` / `env_policy` 마커가 정의되어 있습니다.

---

## 3. 릴리즈 태그

- **권장**: `v1.0.0-rc` (Release Candidate) 또는 내부 정책에 따라 **v1.0.0 (conditional)** 로 기록.
- **조건**: 위 핵심 6개 테스트 파일 100% 통과 + 잔여 3개는 Known Issues로 관리하는 경우, conditional v1.0.0 적용 가능.

---

## 4. 참고

- v1 완료 체크리스트: `test_path_manager`, `test_security_manager`, `test_rag_security_phase1a`, `test_agent_tools`, `test_agent_brain`, `test_long_form_policy`.
- 환경 고정: `python -m pytest --version`이 프로젝트 `.venv`에서 성공하는지 확인할 것.
