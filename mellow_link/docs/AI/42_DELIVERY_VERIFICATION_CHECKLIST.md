# 납품 증빙 체크리스트 (최종 4항목)

## 1. 아웃바운드 네트워크 차단 상태에서 엔드투엔드 스모크

| 절차 | 확인 내용 |
|------|-----------|
| 사전 조건 | OS 방화벽으로 **python 프로세스 아웃바운드 차단** |
| 동작 시나리오 | run / approve / reject / web_search / **media_ai** / **upload** 호출 |
| 기대 결과 | 각 기능이 **의도대로 차단**되고, **시스템은 정상 동작**(크래시/무한 대기 없음) |

- run/approve/reject: 에이전트/오케스트레이터 경로에서 차단 시 명확한 에러 또는 대체 동작.
- web_search: ENABLE_WEB_SEARCH=0 또는 네트워크 차단 시 도구 호출 차단/에러.
- media_ai: ENABLE_MEDIA_AI=0 또는 어댑터 차단 시 `RuntimeError`(메시지에 `ENABLE_MEDIA_AI` 포함).
- upload: ENABLE_MEDIA_UPLOAD=0 시 no-op, 로그에 이유 기록.

### E2E 차단 테스트 실제 실행 결과

플래그 OFF 상태에서 차단 테스트를 실행한 결과를 아래에 남긴다. (방화벽 아웃바운드 차단은 수동 검증 항목.)

| 항목 | 내용 |
|------|------|
| 실행 일시 | 2026-02-26 |
| 환경 | Windows, Python 3.10.11, pytest 9.0.2 |
| 설정 | `ENABLE_MEDIA_AI=0`, `ENABLE_MEDIA_UPLOAD=0`, `ENABLE_FFMPEG=0`, `ENABLE_MEDIA_COMPUTE=1` |
| 명령 | `pytest tests/test_media_adapters.py tests/test_video_processor.py -v --tb=short` |

**실행 로그 (요약):**

```
============================= test session starts =============================
platform win32 -- Python 3.10.11, pytest-9.0.2, pluggy-1.6.0
rootdir: D:\AI_Project\mellow_link
configfile: pytest.ini
plugins: anyio-4.12.1, asyncio-1.3.0
collecting ... collected 7 items

tests/test_media_adapters.py::TestMediaAIAdapterBlock::test_direct_image_service_generate_image_raises_when_media_ai_disabled PASSED [ 14%]
tests/test_media_adapters.py::TestMediaAIAdapterBlock::test_direct_image_service_generate_raises_when_media_ai_disabled PASSED [ 28%]
tests/test_media_adapters.py::TestMediaAIAdapterBlock::test_direct_video_service_generate_video_raises_when_media_ai_disabled PASSED [ 42%]
tests/test_media_adapters.py::TestMediaAIAdapterBlock::test_generate_image_raises_with_flag_name PASSED [ 57%]
tests/test_media_adapters.py::TestMediaUploadNoOp::test_upload_s3_returns_none_and_logs_reason PASSED [ 71%]
tests/test_media_adapters.py::TestFFmpegBlockWhenComputeOnFfmpegOff::test_transcode_video_raises_with_ffmpeg_flag PASSED [ 85%]
tests/test_video_processor.py::TestVideoProcessor::test_missing_ffprobe_returns_original PASSED [100%]

============================== 7 passed, 2 warnings in 0.67s ========================
```

**검증 내용 요약:**

| 테스트 | 검증 내용 |
|--------|-----------|
| test_direct_image_service_generate_raises_when_media_ai_disabled | `ImageService().generate()` 직접 호출 시 ENABLE_MEDIA_AI=0 → RuntimeError, 메시지에 플래그명 포함 |
| test_direct_image_service_generate_image_raises_when_media_ai_disabled | `ImageService().generate_image()` 직접 호출 시 동일 차단 |
| test_direct_video_service_generate_video_raises_when_media_ai_disabled | `VideoService().generate_video()` 직접 호출 시 동일 차단 |
| test_generate_image_raises_with_flag_name | 어댑터 `generate_image(None)` 호출 시 RuntimeError + ENABLE_MEDIA_AI/0 포함 |
| test_upload_s3_returns_none_and_logs_reason | ENABLE_MEDIA_UPLOAD=0 시 upload_s3 no-op, None 반환, 로그에 이유 기록 |
| test_transcode_video_raises_with_ffmpeg_flag | ENABLE_MEDIA_COMPUTE=1 & ENABLE_FFMPEG=0 시 transcode_video 호출 → RuntimeError(ENABLE_FFMPEG 포함) |
| test_missing_ffprobe_returns_original | compute 어댑터가 FileNotFoundError 시 원본 경로 반환(시스템 살아있음) |

**결론:** 7건 모두 통과. 플래그 OFF 시 media_ai/upload/ffmpeg 경로가 의도대로 차단되고, 프로세스는 정상 종료(크래시 없음).

---

## 2. ComfyUI가 로컬로만 붙는지

| 확인 항목 | 내용 |
|-----------|------|
| ComfyMediaAIAdapter | ImageService/VideoService 인스턴스 생성 시 **인자 없음** → `DEFAULT_HOST=localhost`, `DEFAULT_PORT=8188` 사용. 즉 **127.0.0.1:8188**만 사용. |
| 설정 오버라이드 | ComfyUI 주소를 바꾸는 코드 경로는 **없음**. (필요 시 ImageService/VideoService 생성부에 host/port 주입 가능하나, 기본은 로컬 전용.) |
| 모델 다운로드 | **Mellow Link는 ComfyUI로 HTTP/WS만 호출.** 모델 다운로드는 **ComfyUI 프로세스 자체**의 “첫 실행 시” 동작. ComfyUI를 로컬에서만 실행하고, 해당 프로세스의 아웃바운드를 별도 정책으로 제어하면 “첫 실행 외부 접속” 여부를 제어 가능. |

---

## 3. FFmpeg 바이너리 의존성

| 항목 | 내용 |
|------|------|
| 현재 기본값 | `ENABLE_FFMPEG` 기본값 **True** (config/settings.py). |
| 납품 패키지 | ffmpeg/ffprobe **포함 여부**를 납품 스펙에서 명시. |
| 미포함 시 권장 | 패키지에 ffmpeg/ffprobe를 **포함하지 않으면** 기본값을 **ENABLE_FFMPEG=0**으로 재검토 권장. 운영자가 PATH 또는 MELLOW_FFMPEG_PATH/MELLOW_FFMPEG_BIN_DIR 설정 후 **ENABLE_FFMPEG=1**로 켜서 사용. |

---

## 4. 문서 스냅샷 — 플래그 OFF 시 외부 호출 제어

**“외부 호출은 플래그 OFF면 로드/초기화/실행 불가”** 요약표 (1장).

| 플래그 | OFF(0) 시 동작 | ON(1) 시 |
|--------|-----------------|----------|
| **ENABLE_MEDIA_AI** | NullMediaAIAdapter. generate_image/generate_video 등 호출 시 **RuntimeError**(메시지에 플래그명 포함). 서비스 직접 호출도 동일 차단. | ComfyMediaAIAdapter. ImageService/VideoService 위임. |
| **ENABLE_MEDIA_UPLOAD** | NullUploadAdapter. upload_s3 등 **no-op**, 반환 None, 로그에 이유 기록. 외부 전송 없음. | 실구현(현재는 Null; 추후 S3/YouTube 등). |
| **ENABLE_MEDIA_COMPUTE** | NullMediaComputeAdapter. transcode/extend/probe 등 호출 시 **RuntimeError**. | LocalFFmpegComputeAdapter 사용(ENABLE_FFMPEG 따름). |
| **ENABLE_FFMPEG** | allow_media_compute()=1이어도 ffmpeg/ffprobe 경로 **호출 시 RuntimeError**. | LocalFFmpegComputeAdapter 내 ffmpeg/ffprobe 실행 허용. |
| **ENABLE_WEB_SEARCH** | 웹 검색 도구 호출 차단(정책/가드). | 웹 검색 도구 사용 가능. |
| **Ollama/LLM** | 별도 플래그 없음. 연결 실패 시 연결 오류. (폐쇄망에서는 로컬 Ollama만 사용 전제.) | — |

- **로드/초기화**: Factory에서 OFF면 해당 어댑터는 Null(no-op/차단) 인스턴스만 생성. 외부 클라이언트/키 로드는 하지 않음.
- **실행**: OFF 상태에서 해당 기능 API를 호출하면 위 표와 같이 RuntimeError 또는 no-op으로 **실행 불가**.
