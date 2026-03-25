# pytest 재현용 명령 (동일 환경에서 57 passed 등 재현)

**전제**:
- 셸의 현재 디렉터리는 **저장소 루트**(`D:\AI_Project` 등)여야 합니다.
- **pytest**가 실행에 사용되는 Python 환경에 설치되어 있어야 합니다.  
  `.venv` 사용 시: `pip install pytest` 후 `python -m pytest` 또는 활성화된 venv에서 `pytest` 실행.

---

## 1. PathManager 테스트만 (57개)

```bash
# 저장소 루트에서 (pytest가 현재 Python에 설치된 경우)
python -m pytest mellow_link/tests/test_path_manager.py -q --tb=line
```

또는 (시스템/다른 환경의 pytest 사용 시):

```bash
pytest mellow_link/tests/test_path_manager.py -q --tb=line
```

- **경로**: `mellow_link/tests/test_path_manager.py` (루트 기준 상대 경로)
- **옵션**: `-q`(요약), `--tb=line`(짧은 traceback)
- **마커/ignore**: 없음
- **`No module named pytest`** 나오면: 사용 중인 Python에 `pip install pytest` 후 다시 실행.

---

## 2. 보안 회귀 테스트 (security + RAG + auth E2E)

```bash
# 저장소 루트에서
python -m pytest mellow_link/tests/test_security_manager.py mellow_link/tests/test_rag_security_phase1a.py mellow_link/tests/test_auth_e2e.py -q --tb=short
```

- **경로**: 위 세 파일 나열
- **참고**: `test_auth_e2e.py`는 앱 로드 가능할 때만 실행됨(psutil 등 의존성 필요). 실패 시 해당 테스트는 스킵.

---

## 3. PathManager + 보안 통합 (한 번에)

```bash
python -m pytest mellow_link/tests/test_path_manager.py mellow_link/tests/test_security_manager.py mellow_link/tests/test_rag_security_phase1a.py mellow_link/tests/test_auth_e2e.py -v --tb=short
```

- `-v`: 테스트별 결과 출력
- **기대**: path_manager 57 + security_manager 약 15 + RAG 약 13 + auth_e2e 9 → 환경에 따라 일부 스킵 가능

---

## 4. Windows PowerShell에서 (저장소 루트로 이동 후)

```powershell
cd D:\AI_Project
python -m pytest mellow_link/tests/test_path_manager.py -q --tb=line
```

---

## 실패 시 확인

- **현재 디렉터리**: `python -c "import pathlib; print(pathlib.Path.cwd())"` → 루트여야 함.
- **import 확인**: `python -c "from mellow_link.core.path_manager import PathManager; print('ok')"` → `ok` 출력되어야 함.
- **Python 경로**: `python`은 가상환경 또는 `mellow_link` 의존성이 설치된 인터프리터를 사용해야 함.
- **No module named pytest**: `.venv` 등 현재 사용 중인 Python에 pytest가 없음.  
  - `pip install pytest` 후 `python -m pytest ...` 재실행,  
  - 또는 이미 pytest가 설치된 다른 환경에서 `pytest ...` 로 실행.
