# 경로 정규화 및 보안 레이어 개선 완료

**수정 일시**: 2026-02-09  
**문제**: SLM 에이전트가 `"."`, `"workspace"` 같은 상대 경로를 입력할 때 보안 엔진이 이를 차단하는 루프 발생

---

## 🔧 수정 사항

### 1. `agent_brain.py` - 경로 정규화 함수 완전 교체

#### `_normalize_path()` 함수 재구현
- **입력**: `"."`, `"./"`, `"workspace"`, `"workspace/"` → `BASE_PATH`로 직접 치환
- **접두어 제거**: `"workspace/"` 접두어 자동 제거 후 BASE_PATH와 결합
- **보안 검증**: `resolve()`로 `..` 탈출 차단, `startswith()`로 BASE_PATH 내부 확인
- **성공 메시지**: 경로 수정 시 에러 대신 성공 메시지 반환 (루프 방지)

#### `_normalize_and_validate_path_args()` 함수 개선
- 반환값에 `correction_msg` 추가
- 여러 경로 수정 시 모든 메시지 통합

#### 도구 실행 전처리 강화
- 경로 정규화 후 수정 메시지를 Observation에 자동 추가
- 성공 메시지로 루프 방지

### 2. `agent_tools.py` - 도구 레벨 경로 정규화 추가

#### 공통 함수 추가
- `_normalize_workspace_path()`: 상대 경로를 workspace 기준 절대 경로로 정규화

#### 수정된 도구들
1. ✅ `read_file()` - 경로 정규화 추가
2. ✅ `write_file()` - 경로 정규화 추가
3. ✅ `list_directory()` - 경로 정규화 추가
4. ✅ `cleanup_file()` - 경로 정규화 추가 (propose, execute 모드 모두)
5. ✅ `animate_image()` - 경로 정규화 추가

---

## 📋 작동 방식

### 경로 정규화 프로세스

```
입력: "."
  ↓
_normalize_workspace_path()
  ↓
BASE_PATH (D:\AI_Project\mellow_link\workspace)
  ↓
resolve_for_read()
  ↓
성공 메시지: "[경로 자동 교정] '.' → 'D:\AI_Project\mellow_link\workspace'"
```

### 이중 방어 메커니즘

1. **agent_brain.py 레벨**: 도구 실행 전 경로 정규화
2. **agent_tools.py 레벨**: 도구 내부에서도 경로 정규화 (안전장치)

---

## ✅ 해결된 문제

### Before (문제 상황)
```
입력: "."
  ↓
resolve_for_read(".") → mellow_link/. (sandbox 기준)
  ↓
_ensure_path_inside_workspace() → 실패
  ↓
에러: "[ERROR] 너는 허가되지 않은 경로 '.'에 접근하려 했다"
  ↓
루프 발생
```

### After (해결)
```
입력: "."
  ↓
_normalize_workspace_path(".") → D:\AI_Project\mellow_link\workspace
  ↓
resolve_for_read(절대경로) → 성공
  ↓
성공 메시지: "[경로 자동 교정] '.' → 'D:\AI_Project\mellow_link\workspace'"
  ↓
정상 실행
```

---

## 🎯 지원하는 경로 형식

| 입력 | 정규화 결과 |
|------|------------|
| `"."` | `D:\AI_Project\mellow_link\workspace` |
| `"./"` | `D:\AI_Project\mellow_link\workspace` |
| `"workspace"` | `D:\AI_Project\mellow_link\workspace` |
| `"workspace/"` | `D:\AI_Project\mellow_link\workspace` |
| `"workspace/file.txt"` | `D:\AI_Project\mellow_link\workspace\file.txt` |
| `"./file.txt"` | `D:\AI_Project\mellow_link\workspace\file.txt` |
| `"file.txt"` | `D:\AI_Project\mellow_link\workspace\file.txt` |

---

## 🔒 보안 검증

1. **심볼릭 링크 차단**: `resolve()`로 실제 경로 확인
2. **경로 탈출 차단**: `..` 패턴 감지 및 차단
3. **BASE_PATH 검증**: `startswith()`로 workspace 내부 확인
4. **이중 방어**: agent_brain + agent_tools 양쪽에서 검증

---

## 📝 변경된 파일

1. `mellow_link/core/agent_brain.py`
   - `BASE_PATH` 상수 정의 (resolve() 적용)
   - `_normalize_path()` 함수 완전 교체
   - `_normalize_and_validate_path_args()` 함수 개선
   - 도구 실행 전처리 강화

2. `mellow_link/core/agent_tools.py`
   - `_normalize_workspace_path()` 공통 함수 추가
   - `read_file()`, `write_file()`, `list_directory()`, `cleanup_file()`, `animate_image()` 경로 정규화 추가

---

## 🧪 테스트 권장 사항

다음 명령으로 테스트:
```
read_file(".")
list_directory(".")
read_file("workspace")
write_file("workspace/test.txt", "test")
```

모든 명령이 정상 작동하고, 경로 자동 교정 메시지가 표시되어야 합니다.

---

## 💡 참고 사항

- 절대 경로가 이미 workspace 내부인 경우 중복 정규화는 발생하지 않음
- 경로 정규화는 투명하게 수행되며, 사용자에게는 성공 메시지로 알림
- 에러 메시지 대신 성공 메시지를 사용하여 루프 방지
