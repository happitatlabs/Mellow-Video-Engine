"""
Ollama 진단 스크립트 - 빈 응답 문제 진단

빈 응답이 발생하는 원인을 확인합니다.
"""

import asyncio
import aiohttp
import json
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

async def test_ollama_connection():
    """Ollama 서버 연결 테스트"""
    base_url = "http://localhost:11434"
    
    print("=" * 60)
    print("Ollama 진단 시작")
    print("=" * 60)
    
    async with aiohttp.ClientSession() as session:
        # 1. 서버 상태 확인
        print("\n[1] Ollama 서버 상태 확인...")
        try:
            async with session.get(f"{base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m.get("name", "") for m in data.get("models", [])]
                    print(f"✅ 서버 연결 성공")
                    print(f"   사용 가능한 모델: {models}")
                else:
                    print(f"❌ 서버 응답 오류: {resp.status}")
                    return False
        except aiohttp.ClientError as e:
            print(f"❌ 서버 연결 실패: {e}")
            print("   → Ollama 서버가 실행 중인지 확인하세요: ollama serve")
            return False
        
        # 2. 간단한 채팅 테스트 (도구 없이)
        print("\n[2] 간단한 채팅 테스트 (도구 없이)...")
        try:
            payload = {
                "model": "qwen2.5:7b",
                "messages": [
                    {"role": "user", "content": "안녕하세요"}
                ],
                "stream": False
            }
            
            async with session.post(
                f"{base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    message = data.get("message", {})
                    content = message.get("content", "")
                    
                    print(f"✅ 채팅 응답 수신")
                    print(f"   응답 길이: {len(content)} 문자")
                    print(f"   응답 내용: {content[:200]}...")
                    
                    if not content:
                        print("⚠️ 빈 응답 감지!")
                        print(f"   전체 응답: {json.dumps(data, ensure_ascii=False, indent=2)}")
                        return False
                else:
                    error_text = await resp.text()
                    print(f"❌ 채팅 API 오류: {resp.status}")
                    print(f"   에러: {error_text[:500]}")
                    return False
        except asyncio.TimeoutError:
            print("❌ 타임아웃 발생 (30초 초과)")
            print("   → 모델이 너무 느리거나 GPU가 사용되지 않을 수 있습니다")
            return False
        except Exception as e:
            print(f"❌ 채팅 테스트 실패: {e}")
            return False
        
        # 3. Tool Calling 테스트
        print("\n[3] Tool Calling 테스트...")
        try:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "test_tool",
                        "description": "테스트 도구",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "검색어"}
                            },
                            "required": ["query"]
                        }
                    }
                }
            ]
            
            payload = {
                "model": "qwen2.5:7b",
                "messages": [
                    {"role": "user", "content": "test_tool 도구를 사용해서 'hello'를 검색해줘"}
                ],
                "tools": tools,
                "stream": False
            }
            
            async with session.post(
                f"{base_url}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    message = data.get("message", {})
                    content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])
                    
                    print(f"✅ Tool Calling 응답 수신")
                    print(f"   Content 길이: {len(content)} 문자")
                    print(f"   Tool calls 개수: {len(tool_calls) if tool_calls else 0}")
                    
                    if tool_calls:
                        print(f"   첫 번째 tool_call: {json.dumps(tool_calls[0], ensure_ascii=False, indent=2)}")
                    
                    if not content and not tool_calls:
                        print("⚠️ 빈 응답 + tool_calls 없음!")
                        print(f"   전체 응답: {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}")
                        return False
                else:
                    error_text = await resp.text()
                    print(f"❌ Tool Calling API 오류: {resp.status}")
                    print(f"   에러: {error_text[:500]}")
                    return False
        except asyncio.TimeoutError:
            print("❌ 타임아웃 발생 (30초 초과)")
            return False
        except Exception as e:
            print(f"❌ Tool Calling 테스트 실패: {e}")
            return False
        
        print("\n" + "=" * 60)
        print("✅ 모든 테스트 통과!")
        print("=" * 60)
        return True

if __name__ == "__main__":
    result = asyncio.run(test_ollama_connection())
    sys.exit(0 if result else 1)
