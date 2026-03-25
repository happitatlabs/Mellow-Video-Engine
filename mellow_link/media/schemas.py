from pydantic import BaseModel, Field
from typing import Any, Dict, Optional


class ImageRequest(BaseModel):
    """
    이미지 생성 요청 스키마
    """

    static_prompt: Optional[str] = Field(
        None,
        description="(권장) 이미지용 정적 프롬프트: 피사체/배경/스타일 중심",
    )
    prompt: str = Field(..., description="(호환) 이미지 프롬프트. static_prompt가 있으면 static_prompt가 우선됨.")
    negative_prompt: Optional[str] = Field(None, description="제외할 요소")
    width: int = Field(1216, description="이미지 너비 (고정): 1216")
    height: int = Field(704, description="이미지 높이 (고정): 704")
    steps: int = Field(20, description="디노이징 스텝 수")
    cfg_scale: float = Field(7.0, description="CFG 스케일")
    seed: int = Field(-1, description="시드 값 (-1은 랜덤)")
    batch_size: int = Field(1, description="한 번에 생성할 매수")
    model: Optional[str] = Field(None, description="사용할 모델 파일명")
    workflow: Optional[str] = Field(None, description="사용할 워크플로우 JSON 파일명 (예: flux_dev_api.json)")
    sampler_name: str = Field("euler", description="샘플러 (예: euler, dpmpp_2m)")
    scheduler: str = Field("normal", description="스케줄러 (예: normal, karras)")
    denoise: float = Field(1.0, description="디노이징 강도 (1.0 = 완전 새로 그리기)")
    provenance: Optional[Dict[str, Any]] = Field(None, description="출력 sidecar용 provenance 정보")


class VideoRequest(BaseModel):
    """
    비디오 생성 요청 스키마 (Image -> Video).
    """

    image_path: str = Field(..., description="입력 이미지 경로 (sandbox 내부 경로 권장)")
    motion_prompt: Optional[str] = Field(
        None,
        description="(권장) 비디오용 모션 프롬프트. LOCAL_MOTION_LOOP에서는 무엇이 국소적으로 움직일지, AMBIENT_STILL_LOOP에서는 미세한 정적 루프 방향을 뜻함.",
    )
    prompt: Optional[str] = Field(
        None,
        description="(호환) 비디오 프롬프트. motion_prompt가 있으면 motion_prompt가 우선됨.",
    )
    mode: Optional[str] = Field(
        None,
        description="비디오 생성 모드. 예: LOCAL_MOTION_LOOP, AMBIENT_STILL_LOOP, VIDEO_ONLY. VIDEO_LOCKED_CAMERA는 deprecated alias.",
    )
    motion_bucket_id: int = Field(127, ge=0, le=255, description="SVD motion bucket id (기본 127)")
    workflow: Optional[str] = Field(None, description="비디오 워크플로우 JSON 파일명 (예: svd.json)")
    width: int = Field(1216, description="비디오 해상도 너비 (고정): 1216")
    height: int = Field(704, description="비디오 해상도 높이 (고정): 704")
    target_duration: float = Field(12.0, ge=0.5, le=60.0, description="목표 비디오 길이(초). 기본 12초")
    loop_mode: str = Field("boomerang", description="루핑 모드: boomerang | crossfade")
    overlap_seconds: float = Field(0.35, ge=0.05, le=2.0, description="crossfade overlap(초)")
    fps: int = Field(8, ge=1, le=60, description="출력 fps (기본 8)")
    provenance: Optional[Dict[str, Any]] = Field(None, description="출력 sidecar용 provenance 정보")
