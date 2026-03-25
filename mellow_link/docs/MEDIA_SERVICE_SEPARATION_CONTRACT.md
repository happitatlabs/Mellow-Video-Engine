# Media Service Separation Contract

## 목적

이 문서는 `image engine` / `video engine`을 현재 `mellow_link` core runtime 밖의 별도 repo/service로 완전히 분리할 때 필요한 최소 계약을 정리한다.

전제:
- media 기능은 core에서 기본적으로 `off` 상태여야 한다.
- media 기능이 켜져도 core는 media 구현 파일을 직접 import하지 않는다.
- 파일은 core repo 내부 경로를 공유하지 않고, 완전히 외부 서비스로 분리한다.
- 따라서 `로컬 파일 경로 전달`이 아니라 `artifact 업로드/다운로드` 또는 `object storage URL` 기반 계약을 사용한다.


## 목표 상태

분리 후 구조:
- `core service`
  - chat/orchestrator/document/rag/auth/system 담당
  - media 기능은 capability 조회 + media API client만 담당
- `media service`
  - image generation
  - video generation
  - ffmpeg post-process
  - ComfyUI 연결 및 workflow 내부 책임
- `shared contract`
  - OpenAPI 또는 shared schema package
  - request/response 모델
  - error code
  - auth header 규약


## 핵심 원칙

### 1. Core는 media 구현을 모른다

core는 아래만 안다:
- media service base URL
- auth token 또는 내부 인증 방식
- API contract
- timeout / retry 정책

core는 아래를 몰라야 한다:
- ComfyUI workflow 파일명
- ffmpeg 경로
- media service 내부 output 디렉토리
- media service 내부 temp 파일 구조
- media model/checkpoint 세부 이름


### 2. 파일 경로를 계약에 넣지 않는다

금지:
- `D:\...` 같은 로컬 절대 경로 전달
- core output path를 media service가 직접 읽는 방식
- media service 로컬 path를 core가 직접 소비하는 방식

권장:
- multipart upload
- presigned URL upload/download
- object storage key
- artifact id 기반 조회


### 3. 긴 작업은 job 기반으로 본다

image/video 생성은 지연이 길고 실패 유형이 다양하므로 synchronous blocking API보다 `submit -> status/result` 모델을 기본으로 한다.

예외:
- 내부망 단일 호스트 데모용으로만 sync API 허용 가능
- 운영 계약은 job API를 기준으로 설계


## 서비스 경계

### Core service 책임

- 사용자 요청 인증/권한 처리
- media 사용 가능 여부 판단
- media 요청 생성
- media job 상태 polling 또는 callback 수신
- 최종 사용자 응답 조합
- media OFF일 때 route/capability 숨김

### Media service 책임

- image/video 요청 validation
- ComfyUI 호출
- workflow 선택 및 prompt injection
- ffmpeg post-process
- artifact 저장
- result metadata 반환
- media 전용 health/status 제공


## API 계약

## 1. Capability

`GET /capabilities`

목적:
- core가 media service 사용 가능 상태를 사전에 확인
- image/video 각각의 enable 여부 노출

예시 응답:

```json
{
  "service": "media-service",
  "healthy": true,
  "features": {
    "image_generation": true,
    "video_generation": true,
    "video_postprocess": true
  },
  "version": "1.0.0"
}
```


## 2. Health

`GET /health`

목적:
- liveness/readiness 확인

예시 응답:

```json
{
  "healthy": true,
  "components": {
    "comfyui": true,
    "storage": true,
    "ffmpeg": true
  },
  "timestamp": "2026-03-17T12:00:00Z"
}
```


## 3. Image Job Submit

`POST /v1/image-jobs`

요청:

```json
{
  "request_id": "req_123",
  "prompt": "cinematic portrait of a silver-haired woman",
  "negative_prompt": "blurry, low quality",
  "width": 1216,
  "height": 704,
  "steps": 20,
  "cfg_scale": 7.0,
  "seed": -1,
  "model": "flux1-dev-fp8.safetensors",
  "options": {
    "priority": "normal"
  }
}
```

응답:

```json
{
  "success": true,
  "job_id": "img_job_001",
  "status": "queued"
}
```


## 4. Video Job Submit

`POST /v1/video-jobs`

입력 이미지는 로컬 경로가 아니라 artifact 참조를 사용한다.

요청:

```json
{
  "request_id": "req_124",
  "input_artifact": {
    "artifact_id": "art_img_001"
  },
  "motion_prompt": "subtle camera push-in, hair moving softly",
  "motion_bucket_id": 127,
  "target_duration": 12.0,
  "loop_mode": "boomerang",
  "overlap_seconds": 0.35,
  "fps": 8,
  "options": {
    "priority": "normal"
  }
}
```

응답:

```json
{
  "success": true,
  "job_id": "vid_job_001",
  "status": "queued"
}
```


## 5. Artifact Upload

### 방식 A. Multipart upload

`POST /v1/artifacts`

용도:
- core 또는 upstream client가 input image/video/audio를 media service에 업로드

응답:

```json
{
  "success": true,
  "artifact_id": "art_img_001",
  "content_type": "image/png",
  "size_bytes": 123456
}
```

### 방식 B. Presigned URL

권장 상황:
- artifact 크기가 큼
- service 간 direct upload/download를 줄이고 싶음

필수 계약:
- `storage_provider`
- `bucket/container`
- `object_key`
- `download_url` 또는 `signed_url`
- `expires_at`


## 6. Job Status

`GET /v1/jobs/{job_id}`

응답:

```json
{
  "job_id": "img_job_001",
  "type": "image",
  "status": "running",
  "progress": 42.5,
  "stage": "comfy_execution",
  "created_at": "2026-03-17T12:00:00Z",
  "updated_at": "2026-03-17T12:00:15Z"
}
```

status enum:
- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`


## 7. Job Result

`GET /v1/jobs/{job_id}/result`

성공 예시:

```json
{
  "job_id": "img_job_001",
  "type": "image",
  "status": "succeeded",
  "artifacts": [
    {
      "artifact_id": "art_out_001",
      "kind": "image",
      "filename": "output.png",
      "content_type": "image/png",
      "size_bytes": 345678,
      "download_url": "https://storage.example/art_out_001"
    }
  ],
  "metrics": {
    "generation_time_ms": 18234,
    "postprocess_time_ms": 0
  },
  "metadata": {
    "seed_used": 123456789
  }
}
```

실패 예시:

```json
{
  "job_id": "vid_job_001",
  "type": "video",
  "status": "failed",
  "error": {
    "code": "MEDIA_TIMEOUT",
    "message": "Video generation timed out",
    "retryable": true
  }
}
```


## 8. Cancel

`POST /v1/jobs/{job_id}/cancel`

응답:

```json
{
  "success": true,
  "job_id": "vid_job_001",
  "status": "cancelled"
}
```


## 인증 계약

최소 권장:
- internal bearer token
- `Authorization: Bearer <token>`

운영 권장:
- mTLS 또는 service mesh identity
- bearer token은 보조 수단

필수 규칙:
- core만 media service 호출 가능
- 외부 사용자 토큰을 media service에 그대로 전달하지 않는다
- core는 내부 서비스용 토큰으로 재서명 또는 교체 호출


## 에러 코드 계약

필수 error code:
- `MEDIA_DISABLED`
- `SERVICE_UNAVAILABLE`
- `VALIDATION_ERROR`
- `INPUT_ARTIFACT_MISSING`
- `INPUT_ARTIFACT_UNREADABLE`
- `QUEUE_FULL`
- `COMFYUI_UNAVAILABLE`
- `FFMPEG_UNAVAILABLE`
- `MEDIA_TIMEOUT`
- `GENERATION_FAILED`
- `POSTPROCESS_FAILED`
- `UNAUTHORIZED`
- `FORBIDDEN`

필드:

```json
{
  "error": {
    "code": "GENERATION_FAILED",
    "message": "ComfyUI execution failed",
    "retryable": false,
    "details": {}
  }
}
```


## Artifact 계약

필수 메타데이터:
- `artifact_id`
- `kind` (`image`, `video`, `audio`, `thumbnail`, `input`)
- `filename`
- `content_type`
- `size_bytes`
- `checksum_sha256`
- `created_at`

권장:
- storage TTL
- retention policy
- access scope

삭제 정책:
- media service는 artifact lifecycle owner
- core는 artifact delete를 직접 하지 않음
- 필요 시 `DELETE /v1/artifacts/{artifact_id}` 같은 관리 API 별도 제공


## 시간/성능 계약

기본 SLA 예시:
- image submit 응답: 2초 이내
- video submit 응답: 2초 이내
- health 응답: 1초 이내
- job status 응답: 1초 이내

timeout 정책:
- core -> media API timeout
  - submit: 10초
  - status/result: 10초
- media 내부 generation timeout
  - image: 10분
  - video: 20분

retry 정책:
- `submit`는 idempotency key가 있을 때만 자동 재시도
- `status`는 retry 가능
- `result`는 retry 가능
- `cancel`은 best-effort


## Idempotency 계약

`POST /v1/image-jobs`
`POST /v1/video-jobs`

헤더:
- `Idempotency-Key: <uuid>`

의미:
- 같은 key + 같은 payload는 같은 job을 재사용하거나 같은 결과를 반환
- network retry 시 중복 생성 방지


## 관측성 계약

모든 요청에 공통으로 포함:
- `request_id`
- `job_id`
- `session_id` optional
- `user_id_hash` optional

로그 규칙:
- core와 media 모두 같은 `request_id`를 남긴다
- media 내부 stage를 명시한다
  - `artifact_upload`
  - `queued`
  - `comfy_execution`
  - `download_outputs`
  - `postprocess`
  - `completed`


## 상태 전이 계약

image/video job 공통:

```text
queued -> running -> succeeded
queued -> running -> failed
queued -> cancelled
running -> cancelled
```

추가 stage는 세부 필드로만 관리하고, 외부 상태 enum은 단순하게 유지한다.


## Core 쪽 구현 규칙

core는 아래만 구현:
- media capability cache
- media client
- submit/status/result polling
- timeout/retry
- media OFF 시 route 숨김

core는 아래를 구현하지 않음:
- workflow 파일 선택
- prompt injection
- ffmpeg 처리
- output 파일 정리


## Media service 쪽 구현 규칙

media service는 아래를 내부 책임으로 가진다:
- workflow selection
- model checkpoint mapping
- ComfyUI prompt formatting
- ffmpeg extension/merge/transcode
- artifact persistence

즉, 현재 repo의 아래 관심사는 최종적으로 media service 내부로 흡수된다:
- `media/services/*`
- `media/adapters/*`
- `media/tools.py` 중 실제 생성 로직


## OpenAPI / Shared Schema 방안

권장 순서:

1. OpenAPI를 single source of truth로 둔다
2. core는 generated client 또는 얇은 typed client 사용
3. media service는 OpenAPI-first 또는 schema-first로 구현

이유:
- 별도 repo가 되면 Python import 공유보다 HTTP contract가 기준이 되어야 함
- versioning이 쉬움
- 하위 호환 관리가 쉬움


## Versioning 계약

URL version:
- `/v1/...`

호환 정책:
- response 필드 추가는 허용
- 필수 필드 삭제/이름 변경은 금지
- breaking change는 `/v2`


## 최소 마이그레이션 순서

1. 현재 `mellow_link.media.*`를 기준 구현으로 유지
2. core에 `MediaClient` 추가
3. facade가 local media implementation 대신 remote client를 사용할 수 있게 추상화
4. artifact API 도입
5. image job API 도입
6. video job API 도입
7. media OFF 시 local route 제거
8. local adapter/service 제거


## 분리 전 최종 체크리스트

- core가 media 로컬 경로를 직접 읽지 않는가
- image/video 입력이 artifact 기반으로 바뀌었는가
- `/generate-image` 동기 API를 job API로 치환할 계획이 있는가
- media OFF 시 route가 숨겨지는가
- media error code가 core에서 분기 가능하게 정리됐는가
- auth token 분리가 되었는가
- storage retention owner가 media service로 명확히 정해졌는가
- OpenAPI 초안이 작성됐는가


## 권장 결정

현재 방향 기준 권장안:
- `별도 repo + 별도 media-service`
- `artifact 기반 입력/출력`
- `job API 기반 비동기 실행`
- `OpenAPI contract 우선`
- `core는 media OFF 기본`

이 구성이 현재 코드 상태와 가장 잘 맞고, core 엔진 안정성을 가장 덜 흔든다.
