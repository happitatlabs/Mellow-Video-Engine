# 멜로우링크 로컬 에이전트 사용 가능 도구 목록 (패)

## 📋 기본 도구 (agent_tools.py)

### 파일 시스템 (filesystem)
1. **read_file** - 파일 읽기
   - `file_path`: 읽을 파일 경로
   
2. **write_file** - 파일 쓰기
   - `file_path`: 저장할 파일 경로
   - `content`: 파일 내용
   
3. **list_directory** - 디렉토리 목록 조회
   - `dir_path`: 조회할 디렉토리 (기본값: ".")
   - `recursive`: 재귀 탐색 여부 (기본값: False)
   - `max_depth`: 최대 깊이 (기본값: 3, 범위: 1-5)
   
4. **generate_report** - 보고서 생성
   - `title`: 보고서 제목
   - `overview`: 개요 (선택)
   - `detail`: 상세 내용 (선택)
   
5. **cleanup_file** - 파일 정리/삭제
   - `mode`: "delete" | "archive" | "verify"
   - `file_paths`: 파일 경로 리스트
   - `reason`: 정리 사유 (선택)
   - `proposal_id`: 제안 ID (선택)

### 시스템 (system)
6. **inspect_system_status** - 시스템 상태 확인
   - 인자 없음
   
7. **run_command** - 터미널 명령 실행
   - `command`: 실행할 명령어
   
8. **get_evolution_proposals_summary** - 진화 제안 요약
   - `limit`: 조회 개수 (기본값: 10)
   
9. **security_status** - 보안 상태 확인
   - 인자 없음

### 메모리 (memory)
10. **search_memory** - RAG 메모리 검색
    - `query`: 검색 쿼리
    - `top_k`: 상위 결과 개수 (기본값: 3)

### 창작 (creative)
11. **create_image** - AI 이미지 생성
    - `prompt`: 이미지 프롬프트
    - `width`: 너비 (기본값: 1024)
    - `height`: 높이 (기본값: 1024)
    
12. **animate_image** - 이미지 애니메이션 생성
    - `image_path`: 이미지 경로
    - `motion_bucket_id`: 모션 버킷 ID (기본값: 127)
    - `target_duration`: 목표 지속 시간 (기본값: 12.0)
    - `loop_mode`: 루프 모드 (기본값: "boomerang")
    - `overlap_seconds`: 겹침 초 (기본값: 0.35)

### 아바타 (avatar)
13. **speak** - 아바타 음성 출력
    - `text`: 말할 텍스트
    - `emotion`: 감정 (기본값: "neutral")
    - 가능한 감정: "neutral", "happy", "sad", "surprised", "angry"

### 에이전트 (agent)
14. **finish** - 작업 완료 및 종료
    - `summary`: 최종 요약
    - ⚠️ 필수: 모든 작업 완료 시 반드시 호출해야 함
    
15. **propose_new_tool** - 새 도구 제안 및 등록
    - `tool_name`: 도구 이름 (함수명과 일치)
    - `description`: 도구 설명
    - `code`: Python 코드 문자열 (함수 정의)
    - `parameters_json`: 파라미터 스키마 JSON (기본값: "{}")
    - ✅ **에이전트가 스스로 도구를 만들 수 있는 핵심 도구**

### 일반 (general)
16. **get_cost_efficiency_briefing** - 가성비 브리핑
    - `cost`: 비용
    - `target_file`: 대상 파일 (선택)
    
17. **get_past_failure_context** - 과거 실패 컨텍스트
    - `target_file`: 대상 파일 (선택)
    - `limit`: 조회 개수 (기본값: 3)

## 🔧 동적 도구 (custom_tools/)

`propose_new_tool`로 생성된 도구들이 `mellow_link/custom_tools/` 폴더에 저장되며, 
동적 레지스트리에 자동으로 로드되어 사용 가능합니다.

## ✅ propose_new_tool 작동 확인

`propose_new_tool`은 다음 프로세스로 작동합니다:

1. **코드 검증** (ToolForge)
   - 문법 검사 (AST)
   - 보안 패턴 차단
   - 샌드박스 테스트 실행

2. **보호자 검수** (GuardianService)
   - 보안 취약점 검사
   - 로직 안정성 검토
   - 승인/거부 결정

3. **등록 및 저장**
   - DB에 VERIFIED 상태로 저장
   - `custom_tools/` 폴더에 .py 파일 생성
   - 동적 레지스트리 hot-reload

4. **즉시 사용 가능**
   - 다음 턴부터 새 도구 사용 가능
   - 시스템 프롬프트에 자동 포함

## ⚠️ 현재 상태

- ✅ `propose_new_tool` 도구는 정상 등록되어 있음
- ✅ 화이트리스트에도 포함되어 있음 (`agent_brain.py` 781번 줄)
- ✅ 시스템 프롬프트에 도구 목록이 포함됨 (`agent_brain.py` 623번 줄)
- ✅ 동적 레지스트리가 활성화되어 있음 (`agent_brain.py` 447번 줄)

**결론: 에이전트는 `propose_new_tool`을 사용해 스스로 도구를 만들 수 있습니다!**
