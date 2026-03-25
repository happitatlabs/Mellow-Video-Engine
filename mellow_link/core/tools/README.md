# WebSearchTool - 웹 검색 및 RAG 통합 도구

## 개요

`WebSearchTool`은 MellowLink Agent System의 Research 모드에서 외부 정보를 수집하고, 수집된 데이터를 RAG(Knowledge Bank)에 자동으로 적립하는 전문 도구입니다.

## 주요 기능

### 1. 웹 검색 (`execute`)
- DuckDuckGo API를 통한 검색 수행
- 병렬 스크래핑 지원 (ThreadPoolExecutor)
- 타임아웃 보호 (기본 10초)

### 2. robots.txt 준수 (`_check_robots`)
- ✅ **verified**: 윤리적 가드레일
- 각 도메인의 robots.txt를 파싱하여 스크래핑 허용 여부 확인
- 캐싱으로 성능 최적화

### 3. 콘텐츠 스크래핑 (`scrape_content`)
- BeautifulSoup4를 통한 HTML 파싱
- 마크다운 형식으로 변환
- 불필요한 태그 제거 (script, style, nav 등)

### 4. RAG 통합 (`integrate_with_rag`)
- `MemoryDatabase`의 `experience_ledger`에 자동 저장
- FTS5 인덱스를 통한 향후 검색 지원
- 메타데이터 보존 (URL, 제목, 스니펫 등)

## 보안 및 리소스 제약

### SecurityManager 연동
- **NORMAL** 레벨 이상에서만 아웃바운드 HTTP 허용
- **HARD** 모드에서는 `MELLOW_HARD_OUTBOUND_HTTP_ALLOW` 환경변수로 오버라이드 가능
- Emergency Lockdown 모드에서 자동 차단

### 리소스 관리
- RTX 5070 Ti (16GB VRAM) 환경 고려
- 병렬 스크래핑 시 ThreadPoolExecutor 사용 (기본 3 워커)
- 개별 요청당 10초 타임아웃 강제
- 최대 콘텐츠 길이 제한 (50,000자)

## 사용 방법

### 기본 사용

```python
from mellow_link.core.tools.web_search_tool import WebSearchTool

tool = WebSearchTool()
results = await tool.execute("Python async await 최신 기능", top_k=5)

for result in results:
    print(f"제목: {result.title}")
    print(f"URL: {result.url}")
    print(f"요약: {result.snippet}")
    if result.content:
        print(f"콘텐츠: {result.content[:200]}...")
```

### RAG 통합

```python
# 검색 및 자동 RAG 저장
results = await tool.execute("2024 AI 트렌드", scrape_content=True)

# 수동 RAG 저장
for result in results:
    if result.content:
        record_id = await tool.integrate_with_rag(
            content=result.content,
            metadata={
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet
            }
        )
        print(f"RAG에 저장됨: {record_id}")
```

### AgentBrain 통합

`agent_tools.py`의 `web_search` 함수가 자동으로 `WebSearchTool`을 사용합니다:

```python
# AgentBrain이 자동으로 호출
result = await agent_brain.run(
    user_input="최신 Python 기능을 찾아줘",
    model_mode="research"  # Research 모드
)
```

## AgentBrain Integration (Research Mode)

### 자동 웹 검색
- Research 모드 선택 시 `orchestrator`가 자동으로 웹 검색 수행
- 검색 결과가 컨텍스트에 포함됨
- AgentBrain이 필요 시 추가 `web_search` 도구 호출 가능

### ReAct 루프 강화
1. `search_memory`로 기존 지식 검색
2. 정보 부족 시 자동으로 `web_search` 호출
3. 검색 결과를 RAG에 저장하여 향후 재사용

## 데이터 구조

### SearchResult

```python
@dataclass
class SearchResult:
    title: str              # 검색 결과 제목
    url: str                # 결과 URL
    snippet: str = ""       # 요약 스니펫
    content: Optional[str] = None  # 스크래핑된 전체 콘텐츠
    metadata: Dict[str, Any] = field(default_factory=dict)  # 추가 메타데이터
    scraped_at: Optional[datetime] = None  # 스크래핑 시각
```

## 의존성

필수 패키지:
- `requests`: HTTP 요청
- `beautifulsoup4`: HTML 파싱
- `urllib.robotparser`: robots.txt 파싱 (표준 라이브러리)

설치:
```bash
pip install requests beautifulsoup4
```

## 예외 처리

- `PermissionError`: SecurityManager가 아웃바운드 HTTP를 차단한 경우
- `ImportError`: 필수 패키지가 설치되지 않은 경우
- `requests.exceptions.RequestException`: 네트워크 오류
- robots.txt 차단: 해당 URL 스크래핑 건너뜀 (에러 아님)

## 성능 최적화

- robots.txt 캐싱: 동일 도메인 재요청 방지
- 병렬 스크래핑: 여러 URL 동시 처리
- 콘텐츠 길이 제한: 메모리 사용량 제어
- 타임아웃 강제: 무한 대기 방지

## 향후 개선 사항

- [ ] Google Custom Search API 지원
- [ ] Serper/Tavily 등 전문 검색 API 통합
- [ ] 검색 결과 캐싱 (중복 검색 방지)
- [ ] 더 정교한 콘텐츠 추출 (PDF, DOCX 등)
- [ ] 검색 결과 품질 평가 및 필터링
