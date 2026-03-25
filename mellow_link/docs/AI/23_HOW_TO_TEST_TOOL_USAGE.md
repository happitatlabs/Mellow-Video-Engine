# 로컬 AI 도구 사용 확인 방법

로컬 AI가 도구를 사용할 수 있는지 확인하는 방법을 안내합니다.

## 방법 1: 웹 UI를 통한 테스트 (권장)

가장 간단한 방법은 웹 UI를 통해 테스트하는 것입니다.

1. **서버 실행**
   ```bash
   cd d:\AI_Project
   python -m mellow_link.main
   ```

2. **웹 브라우저에서 접속**
   - URL: `http://localhost:8000`
   - 로그인 후 채팅 화면으로 이동

3. **테스트 명령어 입력**
   ```
   workspace 폴더의 파일 목록을 보여줘
   ```
   
   또는
   ```
   workspace/test.txt 파일을 읽어줘
   ```

4. **결과 확인**
   - AI가 `list_directory` 또는 `read_file` 도구를 사용했는지 확인
   - 실제 파일 목록이나 파일 내용이 답변에 포함되어야 함

## 방법 2: API를 통한 테스트

REST API를 통해 직접 테스트할 수 있습니다.

```python
import requests
import json

# 로그인
login_response = requests.post(
    "http://localhost:8000/api/auth/login",
    data={
        "username": "your_username",
        "password": "your_password"
    }
)
token = login_response.json()["access_token"]

# 채팅 요청
chat_response = requests.post(
    "http://localhost:8000/api/chat",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "message": "workspace 폴더의 파일 목록을 보여줘",
        "session_id": "test_session"
    }
)

result = chat_response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))
```

## 방법 3: 로그를 통한 확인

서버 로그에서 도구 사용 여부를 확인할 수 있습니다.

**성공적인 도구 사용 로그 예시:**
```
[Turn 1] Native Tool Call: list_directory with args: {'directory_path': 'workspace'}
[Execute] list_directory({'directory_path': 'workspace'})
[Turn 1] Observation: 파일 목록: ...
```

**도구 사용 실패 로그 예시:**
```
[Turn 1] Parse failed - no tool calls detected
[Turn 1] Skipping parse_action due to empty response
```

## 방법 4: 간단한 확인 스크립트

의존성 문제 없이 기본 기능만 확인:

```python
import asyncio
from mellow_link.services.llm_service import LLMService
from mellow_link.core.tool_registry import ToolRegistry

async def quick_test():
    # LLM 서비스 연결
    llm = LLMService()
    await llm.connect()
    
    # 도구 등록
    registry = ToolRegistry()
    
    @registry.register
    def test_tool(message: str) -> str:
        """테스트 도구"""
        return f"도구 실행됨: {message}"
    
    # 도구 스키마 생성
    tools_schema = registry.get_tools_schema()
    
    # 테스트 요청
    response = await llm.chat(
        messages=[{"role": "user", "content": "test_tool 도구를 '안녕' 메시지로 호출해줘"}],
        model="qwen2.5:7b",
        tools=tools_schema
    )
    
    print(f"Tool calls: {response.tool_calls}")
    
    if response.tool_calls:
        print("✅ 도구 호출 성공!")
    else:
        print("⚠️ 도구 호출 없음")
    
    await llm.disconnect()

asyncio.run(quick_test())
```

## 확인 포인트

### ✅ 정상 작동 시

1. **Native Tool Calling이 작동하는 경우:**
   - 로그에 `[Turn X] Native Tool Call: ...` 메시지가 보임
   - `response.tool_calls`에 도구 정보가 포함됨
   - 도구가 실제로 실행되고 결과가 Observation에 포함됨

2. **표준 형식 준수:**
   - 메시지 히스토리에 `{"role": "tool", "tool_name": "...", "content": "..."}` 형식이 포함됨
   - `tool_calls`가 없을 때 자동으로 종료됨

### ❌ 문제가 있는 경우

1. **도구가 호출되지 않는 경우:**
   - 모델이 tool calling을 지원하지 않을 수 있음 (qwen2.5:7b 이상 권장)
   - Ollama 버전이 낮을 수 있음 (0.5.0 이상 필요)
   - 도구 스키마 형식이 잘못되었을 수 있음

2. **파싱 실패:**
   - 로그에 `Parse failed` 메시지가 반복됨
   - 기존 `parse_action` 방식으로 폴백됨

## 문제 해결

### 모델 확인
```bash
ollama list
```
- `qwen2.5:7b`, `qwen2.5:14b` 등 tool calling 지원 모델이 있는지 확인

### Ollama 버전 확인
```bash
ollama --version
```
- 0.5.0 이상이어야 Native Tool Calling 지원

### 로그 레벨 변경
더 자세한 로그를 보려면:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 추가 정보

- [Ollama Tool Calling 문서](https://docs.ollama.com/capabilities/tool-calling)
- [구현 상세사항](../core/agent_brain.py)
