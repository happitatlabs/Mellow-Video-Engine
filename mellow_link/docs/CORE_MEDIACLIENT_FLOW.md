# Core MediaClient Interface And Call Flow

## 목적

이 문서는 [MEDIA_SERVICE_OPENAPI_V1.yaml](/D:/AI_Project/mellow_link/docs/MEDIA_SERVICE_OPENAPI_V1.yaml)을 기준으로, `core`가 별도 `media-service`를 호출할 때 필요한 `MediaClient` 인터페이스와 호출 흐름을 정의한다.

범위:
- core 내부 추상화
- media-service 호출 순서
- polling / callback 처리 방식
- media OFF 시 동작
- 실패/재시도 기준

비범위:
- media-service 내부 구현
- ComfyUI / ffmpeg 세부 로직
- object storage 인프라 상세 설정


## 목표

core는 media 구현을 몰라야 한다.

core가 알아야 하는 것은 아래뿐이다:
- media-service base URL
- auth 방식
- OpenAPI 요청/응답 스키마
- timeout / retry / polling 규칙

즉, core 입장에서 media는 아래 인터페이스 하나로 축약된다:
- capability 확인
- artifact 업로드 또는 upload-ticket 발급
- image/video job submit
- job status/result 조회
- cancel


## 설계 원칙

### 1. MediaClient는 유일한 진입점이다

금지:
- router/service/tool에서 직접 HTTP 호출
- `requests` / `httpx`를 여러 위치에서 중복 사용
- media-service URL을 여기저기서 직접 참조

허용:
- `MediaClient` 또는 `NullMediaClient`만 사용


### 2. Media OFF는 client 레벨에서 막는다

`ENABLE_MEDIA_AI=0` 또는 media base URL 미설정이면:
- real client 생성 금지
- `NullMediaClient` 반환
- route 미등록
- capability 응답도 off로 간주


### 3. Core는 job orchestration만 담당한다

core 책임:
- submit
- polling 또는 callback 수신
- 최종 사용자 응답 조립
- timeout / retry 판단

media-service 책임:
- generation
- workflow
- artifact 저장
- result 구성


## 권장 파일 위치

예상 위치:
- `mellow_link/integrations/media_client.py`
- 또는 `mellow_link/clients/media_client.py`

권장 이유:
- `services/`나 `media/` 아래에 두면 local media 구현과 개념이 섞임
- `integrations` 또는 `clients`는 외부 서비스 경계를 표현하기 좋음


## 인터페이스 초안

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Literal, Any


JobType = Literal["image", "video"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


@dataclass(frozen=True)
class MediaCapability:
    healthy: bool
    image_generation: bool
    video_generation: bool
    video_postprocess: bool
    version: str


@dataclass(frozen=True)
class UploadedArtifact:
    artifact_id: str
    content_type: str
    size_bytes: int
    filename: str
    download_url: Optional[str] = None


@dataclass(frozen=True)
class UploadTicket:
    artifact_id: str
    method: str
    url: str
    headers: dict[str, str]
    form_fields: dict[str, str]
    expires_at: str


@dataclass(frozen=True)
class MediaJobAccepted:
    job_id: str
    status: JobStatus


@dataclass(frozen=True)
class MediaJobSnapshot:
    job_id: str
    job_type: JobType
    status: JobStatus
    progress: Optional[float]
    stage: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class MediaJobResult:
    job_id: str
    job_type: JobType
    status: JobStatus
    artifacts: list[dict[str, Any]]
    metrics: dict[str, Any]
    metadata: dict[str, Any]
    error: Optional[dict[str, Any]] = None


class MediaClient(Protocol):
    async def get_capabilities(self) -> MediaCapability: ...
    async def upload_artifact(
        self,
        *,
        content: bytes,
        filename: str,
        content_type: str,
        kind: str = "input",
        checksum_sha256: str | None = None,
        request_id: str | None = None,
    ) -> UploadedArtifact: ...
    async def create_upload_ticket(
        self,
        *,
        content_type: str,
        size_bytes: int,
        kind: str = "input",
        source_filename: str | None = None,
        checksum_sha256: str | None = None,
        request_id: str | None = None,
    ) -> UploadTicket: ...
    async def submit_image_job(self, payload: dict[str, Any], *, request_id: str | None = None, idempotency_key: str | None = None) -> MediaJobAccepted: ...
    async def submit_video_job(self, payload: dict[str, Any], *, request_id: str | None = None, idempotency_key: str | None = None) -> MediaJobAccepted: ...
    async def get_job(self, job_id: str) -> MediaJobSnapshot: ...
    async def get_job_result(self, job_id: str) -> MediaJobResult: ...
    async def cancel_job(self, job_id: str) -> MediaJobSnapshot: ...
```


## 구현 클래스 권장안

### 1. RemoteMediaClient

역할:
- 실제 HTTP 호출
- auth header 주입
- timeout / retry
- 응답 스키마 파싱

### 2. NullMediaClient

역할:
- media OFF 상태 표현
- 모든 mutation 호출에서 `MEDIA_DISABLED` 예외
- capability는 `healthy=False` 또는 off capability 반환

### 3. MediaClientFactory

역할:
- 설정 기반 client 선택

규칙:
- `ENABLE_MEDIA_AI=0` -> `NullMediaClient`
- `MEDIA_SERVICE_URL` 없음 -> `NullMediaClient`
- 둘 다 충족 -> `RemoteMediaClient`


## 예외 모델

권장:

```python
class MediaClientError(RuntimeError):
    code: str
    retryable: bool


class MediaDisabledError(MediaClientError):
    pass


class MediaUnavailableError(MediaClientError):
    pass


class MediaValidationError(MediaClientError):
    pass


class MediaTimeoutError(MediaClientError):
    pass
```

매핑 기준:
- `MEDIA_DISABLED` -> `MediaDisabledError`
- `SERVICE_UNAVAILABLE` / `COMFYUI_UNAVAILABLE` -> `MediaUnavailableError`
- `VALIDATION_ERROR` -> `MediaValidationError`
- `MEDIA_TIMEOUT` -> `MediaTimeoutError`


## 호출 흐름

## 1. Image 생성 흐름

### A. direct upload 방식

```text
Client
  -> Core API
  -> Core validates request
  -> Core checks MediaClient capability/cache
  -> Core submits image job
  -> Core gets job_id
  -> Core polls status/result or waits callback
  -> Core returns final artifact metadata / URL
```

### B. upload-ticket 방식

```text
Client
  -> Core API
  -> Core requests upload-ticket from media-service
  -> Client or Core uploads binary to storage
  -> Core submits image job with uploaded artifact reference if needed
  -> Core polls status/result or waits callback
```

image는 텍스트만으로 생성 가능하므로 artifact upload는 필수가 아니다.


## 2. Video 생성 흐름

권장 기본:

```text
Client
  -> Core API
  -> Core uploads source image as artifact or receives storage ref
  -> Core submits video job with input artifact/storage ref
  -> Core receives accepted(job_id)
  -> Core polls status or waits callback
  -> Core fetches final result
  -> Core returns output artifact metadata / download URL
```

video는 입력 이미지가 필요하므로 아래 둘 중 하나가 항상 필요하다:
- artifact_id
- storage_ref


## 3. Callback 흐름

권장:

```text
Core submit job
  -> options.callback.url 포함
  -> media-service executes job
  -> media-service sends webhook
  -> core validates signature + timestamp
  -> core stores job event / updates state
  -> core fetches final result if event == succeeded
```

callback event types:
- `job.queued`
- `job.running`
- `job.succeeded`
- `job.failed`
- `job.cancelled`


## 4. Polling 흐름

callback이 없거나 실패했을 때:

```text
submit
  -> wait backoff interval
  -> GET /v1/jobs/{job_id}
  -> if running: continue
  -> if succeeded: GET /v1/jobs/{job_id}/result
  -> if failed/cancelled: stop and map error
```

권장 backoff:
- 1s
- 2s
- 3s
- 5s
- 이후 5s 고정


## Timeout / Retry 기준

### submit
- timeout: 10초
- retry: idempotency key 있을 때만 1~2회

### status / result
- timeout: 10초
- retry: 가능

### cancel
- timeout: 10초
- retry: best-effort 1회

### callback
- media-service -> core callback timeout: 5초 권장
- 실패 시 media-service가 재시도


## Core 내부 상태 모델 권장

core가 저장하면 좋은 최소 필드:
- `job_id`
- `request_id`
- `job_type`
- `status`
- `progress`
- `stage`
- `artifact_ids`
- `last_error_code`
- `last_error_message`
- `created_at`
- `updated_at`


## 라우터별 사용 원칙

### image/video 라우터
- `MediaClient`만 사용
- media-service URL 직접 참조 금지
- callback signature 로직 직접 구현 금지

### system/status
- media capability/status는 `MediaClient` 또는 capability cache를 통해서만 노출
- media OFF면 route 또는 status field 자체를 비워 둠

### agent tools
- 직접 local service 호출 대신 future에는 `MediaClient` 기반으로 전환 가능
- 단, 이 단계는 repo/service 분리 직전 또는 직후에 수행


## callback 보안 규칙

core가 구현해야 할 것:
- `X-Media-Signature` 검증
- `X-Media-Timestamp` 허용 오차 검사
- `X-Event-Id` 중복 제거

권장:
- 5분 이내 timestamp만 허용
- event id dedupe TTL 24시간
- 실패한 callback payload는 감사 로그 저장


## MediaClient 도입 후 마감선

이번 분리 작업에서 안전한 마감선은 아래까지다:

### 마감 가능
- OpenAPI 문서 확정
- `MediaClient` 인터페이스 문서 확정
- core에 `NullMediaClient` / `RemoteMediaClient` 설계 확정
- media OFF 기본 정책 확정
- artifact/job/callback 계약 확정

### 아직 구현 안 해도 됨
- 실제 remote media-service repo 생성
- callback receiver 구현
- storage presigned URL 인프라 구축
- local media path 완전 제거

즉, 현재 단계에서 "문서 기준 마감"은 충분히 가능하다.


## 실제 구현 포함 마감선

개발 마감을 기능 기준으로 잡으면 최소 아래까지 필요하다:

1. `MediaClient` 구현
2. image/video route가 local media 대신 `MediaClient`를 사용
3. media OFF 시 route 미노출 유지
4. 최소 1개 경로는 artifact/job 방식으로 실동작
5. 실패/timeout/error code 매핑 테스트

이 아래에서 마감하면 "설계 완료"는 맞지만 "분리 완료"는 아니다.


## 추천 마감 정의

### 1. 설계 마감
- separation contract 문서 완료
- OpenAPI v1 초안 완료
- MediaClient interface/flow 문서 완료

현재 여기까지 오면 된다.

### 2. 코드 마감 1차
- core에 `MediaClient` abstraction 추가
- media route 하나를 remote client 기반으로 전환
- local media는 fallback 또는 disabled

### 3. 코드 마감 2차
- image/video 전부 remote client 전환
- local media 구현 제거 또는 별도 dev-only fallback화


## 결론

현재 시점 기준으로는:
- 문서 마감은 충분히 가능
- "분리 설계 완료"라고 말할 수 있다
- 하지만 "별도 repo/service 분리 구현 완료"라고 하려면 아직 `MediaClient` 코드와 실제 remote 호출 전환이 남아 있다

따라서 이번 턴 이후의 가장 합리적인 마감 표현은 아래다:

`media 분리를 위한 계약/인터페이스 설계 완료`

구현 마감 표현은 아래부터 가능하다:

`core가 local media 구현을 더 이상 직접 사용하지 않고 remote media-service client로 전환 완료`
