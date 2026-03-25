# Tool Output Limits (p95 Latency Patch)

도구(tool) 출력 크기를 제한하여 모델로 전달되는 토큰 수를 줄이고, p95 지연을 개선합니다.  
외부 API가 아닌 **로컬 도구 결과**에만 적용됩니다.

## 기본 상한

| 도구/대상 | 기본 최대 개수 | 환경 변수 |
|-----------|----------------|-----------|
| `list_directory` (flat/recursive) | 50 | `FS_LIST_MAX_ITEMS` |
| 최근 수정 파일/제안 요약 등 | 30 | `FS_RECENT_MAX_ITEMS` |
| 프로세스 목록 등 (향후) | 20 | `SYS_PROC_MAX_ITEMS` |

## 환경 변수로 상한 변경

`.env` 또는 시스템 환경 변수로 덮어쓸 수 있습니다.

```bash
# list_directory 최대 항목 수 (기본 50, 범위 1~500)
FS_LIST_MAX_ITEMS=80

# 최근 파일/제안 요약 등 최대 항목 수 (기본 30, 범위 1~200)
FS_RECENT_MAX_ITEMS=40

# 프로세스 목록 등 최대 항목 수 (기본 20, 범위 1~100)
SYS_PROC_MAX_ITEMS=30
```

설정은 `mellow_link.config.settings`의 `fs_list_max_items`, `fs_recent_max_items`, `sys_proc_max_items`에 반영됩니다.

## 잘림(Truncation) 시 출력 형식

- **텍스트 출력**  
  기존 목록 텍스트 뒤에 다음 푸터가 붙습니다.  
  - `[TRUNCATED] returned N/total. next_offset=N Results truncated to N items. Narrow your query for more.`
- **메타 정보**  
  - `total_count`: 전체 결과 수  
  - `returned_count`: 이번에 반환한 개수  
  - `truncated`: `true`이면 더 많은 결과가 있음  
  - `next_offset`: 다음 페이지 시작 위치(있을 경우)

LLM은 이 메시지를 보고 “더 있음”을 인식하고, 필요 시 쿼리를 좁히거나 페이지네이션을 요청할 수 있습니다.

## 적용 대상 도구

- **list_directory**  
  - flat 모드: 디렉터리 항목을 `FS_LIST_MAX_ITEMS`개까지 반환 후 잘림 메타 추가.  
  - recursive 모드: 트리 라인 수를 동일 상한으로 자른 뒤 동일 푸터 추가.
- **get_evolution_proposals_summary**  
  - 최근 제안 목록을 `FS_RECENT_MAX_ITEMS`로 제한하고, 초과 시 잘림 푸터 추가.

## 보안·호환

- 샌드박스·경로 정책 등 **도구 보안 규칙은 변경하지 않습니다.**
- **하위 호환**: 반환 타입은 기존처럼 문자열(str)이며, 잘림 시에만 푸터가 추가됩니다.

## 테스트 실행

```bash
# 프로젝트 루트에서 (가상환경 활성화 후)
pytest mellow_link/tests/test_tool_output_limits.py -v
```

- `truncate_list`: 120개 항목에 limit 50 → 50개 반환, `total_count=120`, `truncated=True`, `next_offset=50`  
- `format_truncation_footer`: `[TRUNCATED]`, `returned N/total`, `next_offset` 포함 여부  
- list_directory와 동일한 로직으로 120개 → 50개 + 푸터 문자열 생성 시 메타 포함 여부  

프로젝트 의존성(예: `psutil`)이 설치된 환경에서 실행해야 합니다.
