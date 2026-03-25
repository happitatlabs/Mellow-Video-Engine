# 자가발전(Evolution) 기능 검증 가이드

## 요약

| 단계 | 설명 | API/UI |
|------|------|--------|
| 1 | **자가발전 요청** | `POST /evolution/cycle` |
| 2 | **결재 보고서 확인** | 응답에 Tower/Verdict/Audit 결과 포함 |
| 3 | **승인 및 적용** | `POST /evolution/apply-from-proposal` |

---

## 방법 1: 웹 UI로 검증

1. **Admin 계정으로 로그인**
   - Secretary 폴더가 있는 계정이 Admin

2. **"자가발전 요청" 버튼 클릭**
   - 사이드바에 `자가발전 요청` 버튼 (Admin 전용)

3. **수정 요청 입력**
   - 예: `workspace/README.md에 Evolution 테스트 문구 추가`

4. **결재 보고서 확인**
   - Tower 분석, Verdict 수정안, Audit 검수 결과가 카드로 표시됨
   - `audit_approved: true`이면 **승인** 버튼 활성화

5. **승인 버튼 클릭 → 적용**
   - 실제 파일에 코드가 반영됨

---

## 방법 2: Python 스크립트로 검증

```bash
# 1) Admin 토큰 확보: 로그인 후 응답의 access_token 사용 (API 호출 시 Authorization: Bearer <token> 헤더로 전달)
#    POST /auth/token { "username": "admin_id", "password": "..." }
#    응답: { "access_token": "eyJ...", ... }

# 2) Cycle만 실행 (적용 안 함)
python test_evolution_flow.py --token "YOUR_ACCESS_TOKEN"

# 3) 검수 승인 시 자동 적용까지
python test_evolution_flow.py --token "YOUR_ACCESS_TOKEN" --apply

# 4) 다른 수정 요청으로 테스트
python test_evolution_flow.py -t "YOUR_TOKEN" -r "custom_tools/README.txt에 설명 추가" -a
```

---

## 방법 3: curl로 검증

```bash
# 1) 로그인하여 토큰 확보
TOKEN=$(curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"YOUR_ADMIN","password":"YOUR_PASS"}' \
  | jq -r '.access_token')

# 2) Evolution Cycle
curl -X POST http://localhost:8000/evolution/cycle \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"user_request":"workspace/README.md에 테스트 문구 추가"}' | jq .

# 3) 응답에서 id 복사 후 적용
curl -X POST http://localhost:8000/evolution/apply-from-proposal \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"proposal_id":"위에서_받은_id"}' | jq .
```

---

## 허용 대상 경로

EvolutionManager는 다음 경로만 수정 허용합니다.

- `services/`
- `custom_tools/`
- `workspace/`

`core/`, `config/`, `main.py` 등은 기본적으로 차단됩니다.

---

## 문제 해결

| 증상 | 확인 사항 |
|------|-----------|
| 403 Forbidden | Admin 계정으로 로그인했는지, Bearer 토큰이 유효한지 확인 |
| 제안서 없음 / id 없음 | Tower/Verdict/Audit 중 한 단계 실패 → `data.error` 확인 |
| 적용 불가 (미승인) | `audit_approved: false` → 검수(Audit)에서 거부된 경우 |
| 경로 차단 | `target_file`이 services/, custom_tools/, workspace/ 중 하나여야 함 |

---

## 로그 확인

- `mellow_link/logs/evolution.log` — Evolution 이벤트
- `mellow_link/logs/evolution_proposals/{id}.json` — 저장된 제안서
