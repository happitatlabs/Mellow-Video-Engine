# propose_new_tool 사용 예시

## 📌 기본 사용법

`propose_new_tool`은 에이전트가 필요한 도구가 없을 때 스스로 도구를 만들어 등록하는 도구입니다.

## 예시 1: 워크스페이스 파일 개수 세기 도구

```json
{
  "tool": "propose_new_tool",
  "args": {
    "tool_name": "count_files_in_workspace",
    "description": "워크스페이스 디렉토리의 파일 개수를 세어 반환합니다. 특정 확장자 필터링도 가능합니다.",
    "code": "def count_files_in_workspace(extension: str = None) -> str:\n    from pathlib import Path\n    workspace_root = Path(r\"D:\\AI_Project\\mellow_link\\workspace\")\n    count = 0\n    if extension:\n        # 특정 확장자만 카운트\n        for file_path in workspace_root.rglob(f\"*.{extension.lstrip('.')}\"):\n            if file_path.is_file():\n                count += 1\n    else:\n        # 모든 파일 카운트\n        for file_path in workspace_root.rglob(\"*\"):\n            if file_path.is_file():\n                count += 1\n    return f\"[완료] 워크스페이스 파일 개수: {count}개\" + (f\" (확장자: .{extension})\" if extension else \"\")",
    "parameters_json": "{\"extension\": {\"type\": \"string\", \"default\": null}}"
  }
}
```

## 예시 2: 파일 크기 합계 계산 도구

```json
{
  "tool": "propose_new_tool",
  "args": {
    "tool_name": "calculate_total_size",
    "description": "워크스페이스 내 특정 디렉토리나 파일들의 총 크기를 바이트 단위로 계산합니다.",
    "code": "def calculate_total_size(path: str = \".\") -> str:\n    from pathlib import Path\n    workspace_root = Path(r\"D:\\AI_Project\\mellow_link\\workspace\")\n    target = workspace_root / path if path != \".\" else workspace_root\n    if not target.exists():\n        return f\"[Error] 경로가 존재하지 않습니다: {path}\"\n    total_size = 0\n    if target.is_file():\n        total_size = target.stat().st_size\n    else:\n        for file_path in target.rglob(\"*\"):\n            if file_path.is_file():\n                try:\n                    total_size += file_path.stat().st_size\n                except Exception:\n                    pass\n    size_mb = total_size / (1024 * 1024)\n    return f\"[완료] 총 크기: {total_size:,} 바이트 ({size_mb:.2f} MB)\"",
    "parameters_json": "{\"path\": {\"type\": \"string\", \"default\": \".\"}}"
  }
}
```

## 예시 3: 특정 키워드가 포함된 파일 찾기 도구

```json
{
  "tool": "propose_new_tool",
  "args": {
    "tool_name": "find_files_with_keyword",
    "description": "워크스페이스에서 특정 키워드가 포함된 파일을 찾아 경로 목록을 반환합니다.",
    "code": "def find_files_with_keyword(keyword: str, file_extension: str = None) -> str:\n    from pathlib import Path\n    workspace_root = Path(r\"D:\\AI_Project\\mellow_link\\workspace\")\n    if not keyword:\n        return \"[Error] 키워드를 입력해주세요.\"\n    matches = []\n    pattern = f\"*.{file_extension}\" if file_extension else \"*\"\n    for file_path in workspace_root.rglob(pattern):\n        if file_path.is_file():\n            try:\n                content = file_path.read_text(encoding=\"utf-8\", errors=\"ignore\")\n                if keyword.lower() in content.lower():\n                    rel_path = file_path.relative_to(workspace_root)\n                    matches.append(str(rel_path))\n            except Exception:\n                pass\n    if not matches:\n        return f\"[결과] '{keyword}' 키워드를 포함한 파일을 찾지 못했습니다.\"\n    result = f\"[완료] '{keyword}' 키워드를 포함한 파일 {len(matches)}개 발견:\\n\"\n    result += \"\\n\".join(f\"  - {m}\" for m in matches[:20])\n    if len(matches) > 20:\n        result += f\"\\n  ... 외 {len(matches) - 20}개\"\n    return result",
    "parameters_json": "{\"keyword\": {\"type\": \"string\", \"required\": \"true\"}, \"file_extension\": {\"type\": \"string\", \"default\": null}}"
  }
}
```

## 예시 4: 최근 수정된 파일 목록 조회 도구

```json
{
  "tool": "propose_new_tool",
  "args": {
    "tool_name": "get_recent_files",
    "description": "워크스페이스에서 최근 수정된 파일을 시간순으로 반환합니다.",
    "code": "def get_recent_files(limit: int = 10) -> str:\n    from pathlib import Path\n    from datetime import datetime\n    workspace_root = Path(r\"D:\\AI_Project\\mellow_link\\workspace\")\n    files_with_time = []\n    for file_path in workspace_root.rglob(\"*\"):\n        if file_path.is_file():\n            try:\n                mtime = file_path.stat().st_mtime\n                files_with_time.append((file_path, mtime))\n            except Exception:\n                pass\n    files_with_time.sort(key=lambda x: x[1], reverse=True)\n    result = f\"[완료] 최근 수정된 파일 {min(limit, len(files_with_time))}개:\\n\"\n    for file_path, mtime in files_with_time[:limit]:\n        rel_path = file_path.relative_to(workspace_root)\n        time_str = datetime.fromtimestamp(mtime).strftime(\"%Y-%m-%d %H:%M:%S\")\n        result += f\"  - {rel_path} (수정: {time_str})\\n\"\n    return result.strip()",
    "parameters_json": "{\"limit\": {\"type\": \"int\", \"default\": 10}}"
  }
}
```

## 예시 5: 중복 파일 찾기 도구

```json
{
  "tool": "propose_new_tool",
  "args": {
    "tool_name": "find_duplicate_files",
    "description": "워크스페이스에서 파일명이 중복된 파일들을 찾아 반환합니다.",
    "code": "def find_duplicate_files() -> str:\n    from pathlib import Path\n    from collections import defaultdict\n    workspace_root = Path(r\"D:\\AI_Project\\mellow_link\\workspace\")\n    name_to_paths = defaultdict(list)\n    for file_path in workspace_root.rglob(\"*\"):\n        if file_path.is_file():\n            name_to_paths[file_path.name].append(file_path)\n    duplicates = {name: paths for name, paths in name_to_paths.items() if len(paths) > 1}\n    if not duplicates:\n        return \"[결과] 중복된 파일명을 찾지 못했습니다.\"\n    result = f\"[완료] 중복 파일명 {len(duplicates)}개 발견:\\n\"\n    for name, paths in list(duplicates.items())[:20]:\n        result += f\"\\n  파일명: {name} ({len(paths)}개)\\n\"\n        for path in paths:\n            rel_path = path.relative_to(workspace_root)\n            result += f\"    - {rel_path}\\n\"\n    if len(duplicates) > 20:\n        result += f\"\\n  ... 외 {len(duplicates) - 20}개\"\n    return result",
    "parameters_json": "{}"
  }
}
```

## ⚠️ 중요 사항

### 코드 작성 규칙
1. **함수명은 `tool_name`과 정확히 일치해야 함**
2. **반환값은 반드시 문자열(str)이어야 함**
3. **워크스페이스 경로는 하드코딩: `D:\\AI_Project\\mellow_link\\workspace`**
4. **pathlib 사용 권장** (os.path, os.walk 등은 보안상 제한될 수 있음)
5. **예외 처리 필수**: 파일 접근 실패 시 안전하게 처리

### parameters_json 형식
```json
{
  "param_name": {
    "type": "string|int|float|bool",
    "default": "기본값" 또는 null,
    "required": "true" (기본값이 없을 때)
  }
}
```

### 검증 프로세스
1. **문법 검사**: AST로 Python 문법 확인
2. **보안 검사**: 위험한 패턴 차단 (exec, eval, subprocess 등)
3. **샌드박스 테스트**: 제한된 환경에서 실행 테스트
4. **Guardian 검수**: 보안 취약점 및 로직 안정성 검토
5. **등록 및 저장**: 통과 시 DB 저장 및 custom_tools/에 파일 생성

### 사용 후
- 도구가 성공적으로 등록되면 다음 턴부터 바로 사용 가능
- `custom_tools/` 폴더에 `.py` 파일로 저장됨
- 동적 레지스트리에 자동 로드되어 시스템 프롬프트에 포함됨

## 💡 팁

- **간단한 도구부터 시작**: 복잡한 로직보다는 단순한 유틸리티 함수로 시작
- **기존 도구 재사용**: `read_file`, `list_directory` 등을 활용한 조합 도구 만들기
- **에러 메시지 명확히**: 실패 시 LLM이 이해할 수 있는 명확한 에러 메시지 반환
- **워크스페이스 제한**: 모든 파일 작업은 `mellow_link/workspace` 내부로 제한
