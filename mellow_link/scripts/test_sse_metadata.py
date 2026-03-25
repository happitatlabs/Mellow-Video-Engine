"""
간단한 테스트 스크립트: SSE done 이벤트에서 selected_mode 확인
"""
import asyncio
import aiohttp
import json
import sys

async def test_sse_metadata():
    """SSE done 이벤트에서 selected_mode 확인"""
    api_url = "http://localhost:8000"
    
    payload = {
        "question": "안녕하세요",
        "mode": "auto",
    }
    
    print("=" * 60)
    print("Testing SSE done metadata")
    print("=" * 60)
    print(f"Request: {payload}")
    print()
    
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_url}/chat/ask",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                print(f"❌ HTTP {resp.status}: {await resp.text()}")
                return
            
            print("SSE Stream:")
            print("-" * 60)
            
            done_metadata = None
            async for line in resp.content:
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str or not line_str.startswith("data: "):
                    continue
                
                try:
                    data_str = line_str[6:]  # "data: " 제거
                    data = json.loads(data_str)
                    
                    if data.get("done"):
                        done_metadata = data
                        print(f"\n✅ Done Event Received:")
                        print(json.dumps(data, indent=2, ensure_ascii=False))
                        break
                    elif data.get("chunk"):
                        print(".", end="", flush=True)
                except json.JSONDecodeError:
                    continue
            
            print("\n" + "=" * 60)
            if done_metadata:
                print("\n📊 Metadata Analysis:")
                print(f"  selected_mode: {done_metadata.get('selected_mode')}")
                print(f"  auto_selected: {done_metadata.get('auto_selected')}")
                print(f"  session_id: {done_metadata.get('session_id')}")
                print(f"  message_id: {done_metadata.get('message_id')}")
                
                if done_metadata.get('selected_mode') is None:
                    print("\n⚠️  WARNING: selected_mode is None!")
                if done_metadata.get('auto_selected') is None:
                    print("⚠️  WARNING: auto_selected is None!")
            else:
                print("❌ No done event received")

if __name__ == "__main__":
    asyncio.run(test_sse_metadata())
