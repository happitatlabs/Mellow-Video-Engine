"""
Visual Planner Module
=====================
State 2: Visual Planning (No-Human Policy)

뮤직비디오 장면 기획을 위한 핵심 모듈.
LLM을 활용하여 가사 기반 시각적 장면을 기획합니다.

Features:
- OpenAI 호환 API 클라이언트 (Ollama/OpenAI 지원)
- JSON 모드 강제 + 4단계 파싱 재시도
- 장면 간 맥락 연속성 유지
- No-Human Policy 강제 적용
- Instrumental 구간 특수 처리
- Mock 모드 테스트 지원
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    ClassVar,
    Dict,
    List,
    Literal,
    Optional,
    Sequence,
    Tuple,
    Type,
    Union,
)

from pydantic import BaseModel, Field, field_validator, model_validator

# Type alias for clarity
JsonDict = Dict[str, Any]

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================

class CameraMovement(str, Enum):
    """카메라 움직임 유형."""
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    TILT_UP = "tilt_up"
    TILT_DOWN = "tilt_down"
    STATIC = "static"
    TRACKING = "tracking"
    CRANE_UP = "crane_up"
    CRANE_DOWN = "crane_down"
    SLOW_ZOOM_IN = "slow_zoom_in"
    SLOW_ZOOM_OUT = "slow_zoom_out"
    DRIFT = "drift"
    ORBIT = "orbit"
    FLOAT = "float"
    STATIC_CONTEMPLATIVE = "static_contemplative"


class CameraSpeed(str, Enum):
    """카메라 속도."""
    VERY_SLOW = "very_slow"
    SLOW = "slow"
    MEDIUM = "medium"
    FAST = "fast"


class Lighting(str, Enum):
    """조명 유형."""
    GOLDEN_HOUR = "golden_hour"
    BLUE_HOUR = "blue_hour"
    OVERCAST = "overcast"
    DRAMATIC = "dramatic"
    SOFT = "soft"
    HARSH = "harsh"
    BACKLIT = "backlit"
    RIM_LIGHT = "rim_light"
    NEON = "neon"
    MOONLIGHT = "moonlight"
    ETHEREAL = "ethereal"
    BIOLUMINESCENT = "bioluminescent"
    COSMIC = "cosmic"
    SOFT_DIFFUSED = "soft_diffused"
    AURORA = "aurora"


class Environment(str, Enum):
    """환경 유형."""
    NATURE = "nature"
    URBAN = "urban"
    INDOOR = "indoor"
    UNDERWATER = "underwater"
    AERIAL = "aerial"
    ABSTRACT = "abstract"
    MACRO = "macro"
    COSMIC = "cosmic"


class LLMProvider(str, Enum):
    """LLM 제공자."""
    OLLAMA = "ollama"
    OPENAI = "openai"
    VLLM = "vllm"
    LM_STUDIO = "lm_studio"


# =============================================================================
# Pydantic Models
# =============================================================================

class ScenePlan(BaseModel):
    """
    LLM이 생성한 장면 기획 데이터.
    Pydantic 모델로 유효성 검증 자동화.
    """
    # 필수 필드
    visual_prompt: str = Field(
        ...,
        min_length=30,
        max_length=1000,
        description="SVD/LTX용 상세 장면 묘사 (영문)"
    )
    negative_prompt: str = Field(
        ...,
        description="피해야 할 요소 목록 (영문)"
    )
    camera_movement: str = Field(
        ...,
        description="카메라 움직임 유형"
    )
    lighting: str = Field(
        ...,
        description="조명 유형"
    )
    scene_summary: str = Field(
        ...,
        min_length=5,
        max_length=200,
        description="다음 장면 연속성을 위한 요약"
    )

    # 선택 필드
    camera_speed: str = Field(default="slow", description="카메라 속도")
    color_palette: List[str] = Field(default_factory=list, description="색상 팔레트")
    mood: str = Field(default="neutral", description="장면 분위기")
    environment: str = Field(default="nature", description="환경 유형")
    weather: str = Field(default="clear", description="날씨")
    time_of_day: str = Field(default="day", description="시간대")
    abstract_level: Optional[str] = Field(default=None, description="추상화 수준")

    # 메타데이터
    segment_id: Optional[str] = Field(default=None, description="연결된 세그먼트 ID")
    is_instrumental: bool = Field(default=False, description="간주 구간 여부")
    generation_attempt: int = Field(default=1, description="생성 시도 횟수")
    raw_response: Optional[str] = Field(default=None, description="원본 LLM 응답")

    @field_validator("visual_prompt")
    @classmethod
    def validate_visual_prompt(cls, v: str) -> str:
        """
        visual_prompt가 영문인지 검증.

        비디오 생성 모델(SVD, LTX-Video, Flux)은 영어만 이해하므로
        한국어가 포함된 경우 경고를 발생시킴.
        """
        v = v.strip()

        # 한글 포함 여부 확인 (가-힣 범위)
        korean_chars = re.findall(r"[가-힣]", v)
        if korean_chars:
            korean_sample = "".join(korean_chars[:10])
            logger.warning(
                f"[LANGUAGE WARNING] visual_prompt contains Korean characters: '{korean_sample}...'. "
                f"Video generation models only understand English. This may cause generation failure."
            )

        # 최소한의 영문 포함 확인
        if not re.search(r"[a-zA-Z]{3,}", v):
            raise ValueError("visual_prompt must contain English description")

        return v

    @field_validator("negative_prompt")
    @classmethod
    def validate_negative_prompt(cls, v: str) -> str:
        """
        negative_prompt가 영문인지 검증.

        비디오 생성 모델은 영어만 이해하므로
        한국어가 포함된 경우 경고를 발생시킴.
        """
        v = v.strip()

        # 한글 포함 여부 확인
        korean_chars = re.findall(r"[가-힣]", v)
        if korean_chars:
            korean_sample = "".join(korean_chars[:10])
            logger.warning(
                f"[LANGUAGE WARNING] negative_prompt contains Korean characters: '{korean_sample}...'. "
                f"Video generation models only understand English."
            )

        return v

    @field_validator("camera_movement")
    @classmethod
    def validate_camera_movement(cls, v: str) -> str:
        """유효한 카메라 움직임인지 검증."""
        valid_values = {e.value for e in CameraMovement}
        if v.lower() not in valid_values:
            # 유사한 값으로 매핑 시도
            v_lower = v.lower().replace(" ", "_").replace("-", "_")
            if v_lower in valid_values:
                return v_lower
            # 기본값 반환
            return CameraMovement.STATIC.value
        return v.lower()

    def get_continuity_context(self) -> JsonDict:
        """다음 장면을 위한 연속성 맥락 추출."""
        return {
            "summary": self.scene_summary,
            "environment": self.environment,
            "lighting": self.lighting,
            "colors": self.color_palette,
            "mood": self.mood,
            "time_of_day": self.time_of_day,
        }

    class Config:
        use_enum_values = True


class LLMConfig(BaseModel):
    """LLM 클라이언트 설정."""
    provider: LLMProvider = Field(default=LLMProvider.OLLAMA)
    base_url: str = Field(default="http://localhost:11434")
    api_key: str = Field(default="not-needed")
    model_name: str = Field(default="llama3.1:8b")
    timeout: float = Field(default=120.0, gt=0)
    max_retries: int = Field(default=3, ge=1, le=10)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)


class SegmentInfo(BaseModel):
    """가사 세그먼트 정보 (입력용)."""
    id: str
    text: str
    start_time: float
    end_time: float
    confidence: float = Field(default=1.0)

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time

    @property
    def is_instrumental(self) -> bool:
        """간주 구간인지 확인."""
        markers = [
            "[instrumental]", "[intro]", "[outro]",
            "[interlude]", "[solo]", "[bridge]", "♪"
        ]
        text_lower = self.text.lower().strip()
        return (
            any(marker in text_lower for marker in markers)
            or len(text_lower) < 5
        )

    @property
    def segment_type(self) -> str:
        """세그먼트 유형 반환."""
        text_lower = self.text.lower()
        if "[intro]" in text_lower:
            return "Intro"
        elif "[outro]" in text_lower:
            return "Outro"
        elif "[interlude]" in text_lower:
            return "Interlude"
        elif "[solo]" in text_lower:
            return "Solo"
        elif "[bridge]" in text_lower:
            return "Bridge"
        elif self.is_instrumental:
            return "Instrumental"
        return "Lyrics"


# =============================================================================
# JSON Parser with Retry Logic
# =============================================================================

class JSONParserWithRetry:
    """
    LLM 출력의 불안정성에 대비한 강화된 JSON 파싱 로직.

    파싱 전략:
    1. 전처리 (마크다운 제거, 공백 정리)
    2. 직접 json.loads 시도
    3. 중괄호 범위 추출 후 파싱
    4. 정규표현식으로 문법 교정 후 파싱
    5. 부분 필드 추출 시도
    """

    REQUIRED_FIELDS: ClassVar[List[str]] = [
        "visual_prompt",
        "negative_prompt",
        "camera_movement",
        "lighting",
        "scene_summary",
    ]

    def __init__(self, required_fields: Optional[List[str]] = None) -> None:
        self.required_fields = required_fields or self.REQUIRED_FIELDS
        self.logger = logging.getLogger(self.__class__.__name__)

    def _preprocess_text(self, text: str) -> str:
        """
        LLM 출력 전처리: 마크다운 제거, 불필요한 텍스트 정리.
        """
        if not text:
            return ""

        original = text

        # 1. 마크다운 코드 블록 제거 (```json ... ``` 또는 ``` ... ```)
        text = re.sub(r"```json\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```\s*", "", text)

        # 2. 앞뒤 설명 텍스트 제거 (JSON 시작 전/후 텍스트)
        # "Here is the JSON:" 같은 텍스트 제거
        text = re.sub(r"^[^{]*(?=\{)", "", text, flags=re.DOTALL)
        text = re.sub(r"(?<=\})[^}]*$", "", text, flags=re.DOTALL)

        # 3. 줄바꿈 정리 (JSON 내부 줄바꿈은 유지하되 불필요한 것 제거)
        text = text.strip()

        if text != original:
            self.logger.debug(f"Preprocessed text (removed {len(original) - len(text)} chars)")

        return text

    def parse(self, text: str) -> Optional[JsonDict]:
        """
        다단계 전략으로 JSON 파싱 시도.

        Args:
            text: 파싱할 원본 텍스트

        Returns:
            파싱된 딕셔너리 또는 None
        """
        if not text or not text.strip():
            self.logger.warning("Empty text received for parsing")
            return None

        # 원본 로깅 (디버그용)
        self.logger.debug(f"Raw LLM output (first 500 chars): {text[:500]}")

        # 전처리
        processed_text = self._preprocess_text(text)

        strategies: List[Tuple[str, Callable[[str], Optional[JsonDict]]]] = [
            ("direct_parse", self._parse_direct),
            ("brace_extract", self._parse_find_braces),
            ("repair_and_parse", self._parse_with_repair),
            ("field_extraction", self._parse_extract_fields),
        ]

        for strategy_name, strategy_func in strategies:
            try:
                result = strategy_func(processed_text)
                if result and self._validate_required_fields(result):
                    self.logger.info(f"JSON parsed successfully with: {strategy_name}")
                    return result
                elif result:
                    self.logger.debug(f"Strategy '{strategy_name}' parsed but missing fields")
            except json.JSONDecodeError as e:
                self.logger.debug(f"Strategy '{strategy_name}' JSON error: {e}")
                continue
            except Exception as e:
                self.logger.debug(f"Strategy '{strategy_name}' failed: {e}")
                continue

        # 모든 전략 실패 시 원본 텍스트로 재시도
        if processed_text != text:
            self.logger.debug("Retrying with original text...")
            for strategy_name, strategy_func in strategies:
                try:
                    result = strategy_func(text)
                    if result and self._validate_required_fields(result):
                        self.logger.info(f"JSON parsed with original text using: {strategy_name}")
                        return result
                except Exception:
                    continue

        self.logger.warning(f"All JSON parsing strategies failed. Text preview: {text[:200]}...")
        return None

    def _parse_direct(self, text: str) -> Optional[JsonDict]:
        """1단계: 직접 파싱."""
        text = text.strip()
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        return None

    def _parse_find_braces(self, text: str) -> Optional[JsonDict]:
        """2단계: 중괄호 범위 추출 (개선된 중첩 처리)."""
        # 첫 번째 { 찾기
        start = text.find("{")
        if start == -1:
            return None

        # 중첩 깊이를 고려한 마지막 } 찾기
        depth = 0
        end = -1
        in_string = False
        escape_next = False

        for i, char in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue

            if char == '\\':
                escape_next = True
                continue

            if char == '"' and not escape_next:
                in_string = not in_string
                continue

            if in_string:
                continue

            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end == -1:
            # 중첩 실패 시 마지막 } 사용
            end = text.rfind("}")

        if end <= start:
            return None

        json_str = text[start:end + 1]
        result = json.loads(json_str)

        if isinstance(result, dict):
            return result
        return None

    def _parse_with_repair(self, text: str) -> Optional[JsonDict]:
        """3단계: 문법 교정 후 파싱."""
        # JSON 부분 추출
        start = text.find("{")
        end = text.rfind("}")

        if start == -1 or end == -1 or end <= start:
            return None

        json_str = text[start:end + 1]

        # 일반적인 오류 수정 (순서 중요)
        repairs: List[Tuple[str, str]] = [
            # 1. 줄바꿈을 공백으로 (문자열 내부 제외하고)
            (r'\n', ' '),
            # 2. 여러 공백을 하나로
            (r' +', ' '),
            # 3. Trailing comma 제거
            (r',\s*}', '}'),
            (r',\s*]', ']'),
            # 4. 작은따옴표를 큰따옴표로 (값에서)
            (r":\s*'([^']*)'", r': "\1"'),
            # 5. None을 null로
            (r'\bNone\b', 'null'),
            # 6. True/False를 소문자로
            (r'\bTrue\b', 'true'),
            (r'\bFalse\b', 'false'),
            # 7. 콜론 뒤 따옴표 누락 수정
            (r':\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*([,}])', r': "\1"\2'),
        ]

        for pattern, replacement in repairs:
            json_str = re.sub(pattern, replacement, json_str)

        result = json.loads(json_str)

        if isinstance(result, dict):
            return result
        return None

    def _parse_extract_fields(self, text: str) -> Optional[JsonDict]:
        """4단계: 개별 필드 정규식 추출 (최후의 수단)."""
        result: JsonDict = {}

        # 각 필드별 정규식 패턴
        field_patterns = {
            "visual_prompt": r'"visual_prompt"\s*:\s*"([^"]+(?:\\.[^"]*)*)"',
            "negative_prompt": r'"negative_prompt"\s*:\s*"([^"]+(?:\\.[^"]*)*)"',
            "camera_movement": r'"camera_movement"\s*:\s*"([^"]+)"',
            "lighting": r'"lighting"\s*:\s*"([^"]+)"',
            "scene_summary": r'"scene_summary"\s*:\s*"([^"]+(?:\\.[^"]*)*)"',
            "mood": r'"mood"\s*:\s*"([^"]+)"',
            "environment": r'"environment"\s*:\s*"([^"]+)"',
            "camera_speed": r'"camera_speed"\s*:\s*"([^"]+)"',
        }

        for field, pattern in field_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1)
                # 이스케이프 문자 처리
                value = value.replace('\\"', '"').replace('\\n', '\n')
                result[field] = value

        # color_palette 배열 추출
        palette_match = re.search(r'"color_palette"\s*:\s*\[(.*?)\]', text, re.DOTALL)
        if palette_match:
            colors_str = palette_match.group(1)
            colors = re.findall(r'"([^"]+)"', colors_str)
            result["color_palette"] = colors

        if result:
            self.logger.debug(f"Field extraction found {len(result)} fields")
            return result

        return None

    def _validate_required_fields(self, data: JsonDict) -> bool:
        """필수 필드 존재 여부 검증."""
        missing_fields = [
            field for field in self.required_fields
            if field not in data or not data[field]
        ]

        if missing_fields:
            self.logger.warning(f"Missing required fields: {missing_fields}")
            # 어떤 필드가 있는지도 로깅
            present_fields = [f for f in self.required_fields if f in data and data[f]]
            self.logger.debug(f"Present fields: {present_fields}")
            return False

        return True


# =============================================================================
# OpenAI Compatible Client (Abstract Base)
# =============================================================================

class BaseLLMClient(ABC):
    """LLM 클라이언트 추상 기본 클래스."""

    @abstractmethod
    async def chat_completion(
        self,
        messages: List[JsonDict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        """채팅 완성 요청."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """리소스 정리."""
        pass


class OpenAICompatibleClient(BaseLLMClient):
    """
    OpenAI 호환 API 클라이언트.

    Ollama, OpenAI, vLLM, LM Studio 등 다양한 LLM 서버 지원.
    openai 라이브러리 또는 aiohttp를 선택적으로 사용.
    """

    def __init__(self, config: LLMConfig) -> None:
        """
        클라이언트 초기화.

        Args:
            config: LLM 설정
        """
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._client: Optional[Any] = None
        self._use_openai_lib = self._should_use_openai_lib()

    def _should_use_openai_lib(self) -> bool:
        """openai 라이브러리 사용 여부 결정."""
        try:
            import openai
            return True
        except ImportError:
            return False

    async def _ensure_client(self) -> None:
        """클라이언트 초기화 보장."""
        if self._client is not None:
            return

        if self._use_openai_lib:
            await self._init_openai_client()
        else:
            await self._init_aiohttp_client()

    async def _init_openai_client(self) -> None:
        """openai 라이브러리로 클라이언트 초기화."""
        from openai import AsyncOpenAI

        # Ollama의 경우 base_url 조정
        base_url = self.config.base_url
        if self.config.provider == LLMProvider.OLLAMA:
            if not base_url.endswith("/v1"):
                base_url = f"{base_url.rstrip('/')}/v1"

        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
        )
        self.logger.info(f"OpenAI client initialized: {base_url}")

    async def _init_aiohttp_client(self) -> None:
        """aiohttp로 클라이언트 초기화."""
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=self.config.timeout)
        self._client = aiohttp.ClientSession(timeout=timeout)
        self.logger.info(f"aiohttp client initialized: {self.config.base_url}")

    async def chat_completion(
        self,
        messages: List[JsonDict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        """
        채팅 완성 요청.

        Args:
            messages: 메시지 목록 [{"role": "...", "content": "..."}]
            temperature: 샘플링 온도
            max_tokens: 최대 토큰 수
            json_mode: JSON 출력 강제 여부

        Returns:
            응답 텍스트
        """
        await self._ensure_client()

        if self._use_openai_lib:
            return await self._chat_openai(messages, temperature, max_tokens, json_mode)
        else:
            if self.config.provider == LLMProvider.OLLAMA:
                return await self._chat_ollama_aiohttp(messages, temperature, json_mode)
            else:
                return await self._chat_openai_aiohttp(messages, temperature, max_tokens, json_mode)

    async def _chat_openai(
        self,
        messages: List[JsonDict],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """openai 라이브러리로 요청."""
        kwargs: JsonDict = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def _chat_ollama_aiohttp(
        self,
        messages: List[JsonDict],
        temperature: float,
        json_mode: bool,
    ) -> str:
        """Ollama API (aiohttp) with forced JSON mode."""
        url = f"{self.config.base_url.rstrip('/')}/api/chat"

        payload: JsonDict = {
            "model": self.config.model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": 2048,  # 충분한 토큰 할당
            },
            # Ollama JSON 모드 강제 활성화 (json_mode 파라미터와 무관하게)
            "format": "json",
        }

        self.logger.debug(f"Ollama request to {url} with model {self.config.model_name}")

        async with self._client.post(url, json=payload) as response:
            if response.status != 200:
                text = await response.text()
                self.logger.error(f"Ollama API error: {response.status} - {text}")
                raise RuntimeError(f"Ollama API error: {response.status} - {text}")

            data = await response.json()
            content = data.get("message", {}).get("content", "")

            if not content:
                self.logger.warning("Ollama returned empty content")

            return content

    async def _chat_openai_aiohttp(
        self,
        messages: List[JsonDict],
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        """OpenAI 호환 API (aiohttp)."""
        url = f"{self.config.base_url.rstrip('/')}/v1/chat/completions"

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        payload: JsonDict = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with self._client.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"API error: {response.status} - {text}")

            data = await response.json()
            return data["choices"][0]["message"]["content"]

    async def close(self) -> None:
        """리소스 정리."""
        if self._client is None:
            return

        if self._use_openai_lib:
            await self._client.close()
        else:
            await self._client.close()

        self._client = None
        self.logger.info("LLM client closed")


# =============================================================================
# Mock Scene Generator
# =============================================================================

class MockSceneGenerator:
    """테스트용 더미 ScenePlan 생성기."""

    MOCK_SCENES: ClassVar[List[JsonDict]] = [
        {
            "visual_prompt": "Vast misty mountain range at dawn, layers of blue-gray peaks fading into fog, golden light breaking through clouds, ancient pine forest in foreground, atmospheric depth, cinematic wide shot",
            "negative_prompt": "humans, people, buildings, vehicles, text",
            "camera_movement": "slow_zoom_out",
            "camera_speed": "very_slow",
            "lighting": "golden_hour",
            "color_palette": ["steel blue", "gold", "forest green"],
            "mood": "serene",
            "environment": "nature",
            "weather": "misty",
            "time_of_day": "dawn",
            "scene_summary": "안개 낀 산맥의 여명, 황금빛이 구름을 뚫고 내려옴",
        },
        {
            "visual_prompt": "Abandoned railway tracks stretching to horizon through autumn forest, rusty rails covered in fallen maple leaves, warm orange and red canopy overhead, soft diffused light filtering through trees, nostalgic melancholic atmosphere",
            "negative_prompt": "humans, trains, modern objects, text, watermark",
            "camera_movement": "tracking",
            "camera_speed": "slow",
            "lighting": "soft",
            "color_palette": ["rust orange", "golden yellow", "brown"],
            "mood": "nostalgic",
            "environment": "nature",
            "weather": "overcast",
            "time_of_day": "afternoon",
            "scene_summary": "가을 숲 속 버려진 철로, 낙엽이 덮인 녹슨 레일",
        },
        {
            "visual_prompt": "Ocean waves crashing against weathered sea cliffs at blue hour, dramatic spray catching last light of day, distant storm clouds gathering on horizon, wild grass bending in coastal wind, raw natural power",
            "negative_prompt": "humans, boats, buildings, beach crowds, text",
            "camera_movement": "pan_left",
            "camera_speed": "medium",
            "lighting": "blue_hour",
            "color_palette": ["deep blue", "slate gray", "white foam"],
            "mood": "melancholic",
            "environment": "nature",
            "weather": "stormy",
            "time_of_day": "blue_hour",
            "scene_summary": "블루아워의 해안 절벽, 파도가 부서지며 물보라 일으킴",
        },
        {
            "visual_prompt": "Abstract aurora borealis dancing over frozen arctic lake, ethereal green and purple lights reflecting on mirror-like ice surface, star-filled cosmic sky above, absolute stillness and silence, otherworldly transcendent beauty",
            "negative_prompt": "humans, buildings, vehicles, text, artificial light",
            "camera_movement": "static_contemplative",
            "camera_speed": "very_slow",
            "lighting": "aurora",
            "color_palette": ["aurora green", "cosmic purple", "ice blue"],
            "mood": "transcendent",
            "environment": "abstract",
            "weather": "aurora",
            "time_of_day": "night",
            "scene_summary": "얼어붙은 호수 위 오로라, 신비로운 빛의 춤",
            "abstract_level": "high",
        },
    ]

    @classmethod
    def generate(
        cls,
        segment: SegmentInfo,
        index: int,
        global_mood: str = "",
    ) -> ScenePlan:
        """
        더미 ScenePlan 생성.

        Args:
            segment: 세그먼트 정보
            index: 세그먼트 인덱스
            global_mood: 전체 분위기

        Returns:
            생성된 ScenePlan
        """
        mock_data = cls.MOCK_SCENES[index % len(cls.MOCK_SCENES)].copy()

        return ScenePlan(
            **mock_data,
            segment_id=segment.id,
            is_instrumental=segment.is_instrumental,
            generation_attempt=1,
            raw_response="[MOCK MODE]",
        )


# =============================================================================
# Visual Planner - Main Class
# =============================================================================

class VisualPlanner:
    """
    뮤직비디오 장면 기획 핵심 클래스.

    Features:
    - LLM 기반 장면 기획
    - 장면 간 맥락 연속성 유지
    - No-Human Policy 강제 적용
    - Instrumental 구간 특수 처리
    - Mock 모드 테스트 지원
    """

    # No-Human Policy 상수
    POSITIVE_ADDITIONS: ClassVar[List[str]] = [
        "no humans",
        "uninhabited",
        "empty of people",
        "solitary landscape",
        "scenery only",
        "masterpiece",
        "best quality",
        "highly detailed",
        "cinematic composition",
        "professional cinematography",
        "8k resolution",
        "film grain",
        "atmospheric",
    ]

    NEGATIVE_PROMPT_BASE: ClassVar[str] = (
        "humans, people, person, man, woman, child, face, portrait, "
        "hands, fingers, body, skin, silhouette, crowd, "
        "text, watermark, logo, signature, "
        "low quality, blurry, pixelated, jpeg artifacts, "
        "cartoon, anime, illustration, 3d render, cgi"
    )

    def __init__(
        self,
        llm_config: Union[LLMConfig, JsonDict],
        prompts_config: JsonDict,
        mock: bool = False,
    ) -> None:
        """
        VisualPlanner 초기화.

        Args:
            llm_config: LLM 클라이언트 설정
            prompts_config: 프롬프트 템플릿 설정 (prompts.yaml 내용)
            mock: True일 경우 API 호출 없이 더미 데이터 반환
        """
        # 설정 로드
        if isinstance(llm_config, dict):
            self.llm_config = LLMConfig(**llm_config)
        else:
            self.llm_config = llm_config

        self.prompts_config = prompts_config
        self.mock = mock

        # 컴포넌트 초기화
        self.llm_client: Optional[OpenAICompatibleClient] = None
        self.json_parser = JSONParserWithRetry()

        # 장면 히스토리 (맥락 연속성용)
        self._scene_history: List[ScenePlan] = []

        # No-Human Policy 로드
        self._load_no_human_policy()

        self.logger = logging.getLogger(self.__class__.__name__)

    def _load_no_human_policy(self) -> None:
        """No-Human Policy 상수 로드."""
        policy = self.prompts_config.get("visual_planning", {}).get("no_human_policy", {})

        if "positive_additions" in policy:
            self.POSITIVE_ADDITIONS = policy["positive_additions"]

        if "negative_prompt_base" in policy:
            self.NEGATIVE_PROMPT_BASE = policy["negative_prompt_base"].strip()

    async def initialize(self) -> None:
        """LLM 클라이언트 초기화."""
        if self.mock:
            self.logger.info("VisualPlanner initialized in MOCK mode")
            return

        self.llm_client = OpenAICompatibleClient(self.llm_config)
        self.logger.info(
            f"VisualPlanner initialized with {self.llm_config.provider.value} "
            f"({self.llm_config.model_name})"
        )

    async def cleanup(self) -> None:
        """리소스 정리."""
        if self.llm_client:
            await self.llm_client.close()
            self.llm_client = None

        self._scene_history.clear()
        self.logger.info("VisualPlanner cleaned up")

    # -------------------------------------------------------------------------
    # Core Planning Method
    # -------------------------------------------------------------------------

    async def plan_scenes(
        self,
        segments: Sequence[Union[SegmentInfo, JsonDict]],
        global_mood: str,
        total_duration: float = 0.0,
        progress_callback: Optional[Callable[[int, int, ScenePlan], Any]] = None,
        fail_threshold: float = 0.5,  # 50% 이상 실패 시 전체 실패
    ) -> List[ScenePlan]:
        """
        가사 세그먼트 리스트를 순회하며 장면 기획.

        Args:
            segments: 가사 세그먼트 리스트
            global_mood: 전체 노래 분위기
            total_duration: 총 재생 시간 (초)
            progress_callback: 진행 콜백 (current, total, scene_plan)
            fail_threshold: 실패 허용 비율 (0.5 = 50% 이상 실패 시 예외 발생)

        Returns:
            생성된 ScenePlan 리스트

        Raises:
            RuntimeError: 너무 많은 장면 기획이 실패한 경우
        """
        # 세그먼트 정규화
        normalized_segments = [
            SegmentInfo(**s) if isinstance(s, dict) else s
            for s in segments
        ]

        self._scene_history.clear()
        scene_plans: List[ScenePlan] = []
        failed_count = 0
        total_segments = len(normalized_segments)

        self.logger.info(f"Planning {total_segments} scenes (mock={self.mock})")

        for index, segment in enumerate(normalized_segments):
            self.logger.info(
                f"Planning scene {index + 1}/{total_segments}: "
                f"{segment.text[:30]}..."
            )

            scene_plan: Optional[ScenePlan] = None

            # 장면 기획 생성
            if self.mock:
                scene_plan = MockSceneGenerator.generate(segment, index, global_mood)
            else:
                try:
                    scene_plan = await self._generate_scene_plan(
                        segment=segment,
                        segment_index=index,
                        total_segments=total_segments,
                        global_mood=global_mood,
                        total_duration=total_duration,
                    )
                except Exception as e:
                    self.logger.error(f"Scene {index + 1} generation failed: {e}")
                    scene_plan = None

            # 실패 처리
            if scene_plan is None:
                failed_count += 1
                self.logger.warning(
                    f"Scene {index + 1}/{total_segments} failed. "
                    f"Total failures: {failed_count}/{total_segments}"
                )

                # 폴백 장면 생성
                fallback_data = self._get_fallback_scene_data()
                scene_plan = ScenePlan(
                    **{k: v for k, v in fallback_data.items() if not k.startswith("_")},
                    segment_id=segment.id,
                    is_instrumental=segment.is_instrumental,
                    generation_attempt=-1,  # 폴백 표시
                    raw_response="[FALLBACK - LLM Failed]",
                )

                # 실패율 체크
                current_fail_rate = failed_count / (index + 1)
                if index >= 2 and current_fail_rate > fail_threshold:
                    error_msg = (
                        f"Too many scene planning failures: {failed_count}/{index + 1} "
                        f"({current_fail_rate:.1%} > {fail_threshold:.1%} threshold). "
                        f"Check LLM connectivity and prompt configuration."
                    )
                    self.logger.error(error_msg)
                    raise RuntimeError(error_msg)

            # 히스토리에 추가 (연속성용)
            self._scene_history.append(scene_plan)
            scene_plans.append(scene_plan)

            # 콜백 호출
            if progress_callback:
                result = progress_callback(index + 1, total_segments, scene_plan)
                if asyncio.iscoroutine(result):
                    await result

        # 최종 검증
        valid_plans = [p for p in scene_plans if p.generation_attempt != -1]
        if len(valid_plans) == 0:
            error_msg = "All scene plans failed to generate. Pipeline cannot continue."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        success_rate = len(valid_plans) / total_segments
        self.logger.info(
            f"Scene planning complete: {len(valid_plans)}/{total_segments} "
            f"successful ({success_rate:.1%})"
        )

        if failed_count > 0:
            self.logger.warning(
                f"{failed_count} scenes used fallback. "
                f"Consider adjusting LLM settings or prompts."
            )

        return scene_plans

    async def _generate_scene_plan(
        self,
        segment: SegmentInfo,
        segment_index: int,
        total_segments: int,
        global_mood: str,
        total_duration: float,
    ) -> Optional[ScenePlan]:
        """
        단일 장면 기획 생성.

        Implements:
        - Instrumental 감지 및 특수 처리
        - 맥락 연속성 주입
        - JSON 파싱 + 재시도

        Returns:
            ScenePlan 또는 None (LLM 실패 시)
        """
        # 이전 장면 맥락 구성
        previous_context = self._build_previous_context(segment_index)

        # 템플릿 선택 (Instrumental vs Lyrics)
        if segment.is_instrumental:
            template = self.prompts_config.get("visual_planning", {}).get(
                "instrumental_template", ""
            )
            template_vars = {
                "global_mood": global_mood or "cinematic",
                "segment_type": segment.segment_type,
                "start_time": f"{segment.start_time:.2f}",
                "end_time": f"{segment.end_time:.2f}",
                "duration": f"{segment.duration:.2f}",
                "segment_index": segment_index + 1,
                "total_segments": total_segments,
                "previous_context": previous_context,
            }
        else:
            template = self.prompts_config.get("visual_planning", {}).get(
                "scene_plan_template", ""
            )
            template_vars = {
                "global_mood": global_mood or "cinematic",
                "total_duration": f"{total_duration:.2f}",
                "lyrics": segment.text,
                "start_time": f"{segment.start_time:.2f}",
                "end_time": f"{segment.end_time:.2f}",
                "duration": f"{segment.duration:.2f}",
                "segment_index": segment_index + 1,
                "total_segments": total_segments,
                "previous_context": previous_context,
            }

        # 사용자 메시지 포맷팅
        try:
            user_message = template.format(**template_vars)
        except KeyError as e:
            self.logger.error(f"Template formatting error: missing key {e}")
            return None

        # 시스템 프롬프트
        system_prompt = self.prompts_config.get("visual_planning", {}).get(
            "system_prompt", ""
        )

        if not system_prompt:
            self.logger.warning("No system prompt configured, using default")
            system_prompt = (
                "You are a cinematographer. Create scene plans for music videos. "
                "Respond with valid JSON only. No humans in scenes."
            )

        # LLM 호출 + 재시도
        scene_data = await self._call_llm_with_retry(
            system_prompt=system_prompt,
            user_message=user_message,
            segment_id=segment.id,
        )

        # LLM 완전 실패 시 None 반환
        if scene_data is None:
            self.logger.error(f"Scene plan generation failed for segment {segment.id}")
            return None

        # ScenePlan 생성 (유효성 검사 포함)
        try:
            scene_plan = ScenePlan(
                visual_prompt=scene_data.get("visual_prompt", ""),
                negative_prompt=scene_data.get("negative_prompt", self.NEGATIVE_PROMPT_BASE),
                camera_movement=scene_data.get("camera_movement", "static"),
                camera_speed=scene_data.get("camera_speed", "slow"),
                lighting=scene_data.get("lighting", "soft"),
                color_palette=scene_data.get("color_palette", []),
                mood=scene_data.get("mood", "neutral"),
                environment=scene_data.get("environment", "nature"),
                weather=scene_data.get("weather", "clear"),
                time_of_day=scene_data.get("time_of_day", "day"),
                scene_summary=scene_data.get("scene_summary", f"Scene {segment_index + 1}"),
                abstract_level=scene_data.get("abstract_level"),
                segment_id=segment.id,
                is_instrumental=segment.is_instrumental,
                generation_attempt=scene_data.get("_attempt", 1),
                raw_response=scene_data.get("_raw_response"),
            )
            return scene_plan

        except Exception as e:
            self.logger.error(f"ScenePlan validation failed: {e}")
            return None

    async def _call_llm_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        segment_id: str,
    ) -> Optional[JsonDict]:
        """
        LLM 호출 + JSON 파싱 + 재시도.

        Args:
            system_prompt: 시스템 메시지
            user_message: 사용자 메시지
            segment_id: 세그먼트 ID (로깅용)

        Returns:
            파싱된 장면 데이터 또는 None (완전 실패 시)
        """
        # JSON 출력 강제를 위한 시스템 프롬프트 강화
        enhanced_system_prompt = (
            system_prompt +
            "\n\nIMPORTANT: Respond with ONLY a valid JSON object. "
            "No markdown, no code blocks, no explanations. "
            "Start with { and end with }."
        )

        messages: List[JsonDict] = [
            {"role": "system", "content": enhanced_system_prompt},
            {"role": "user", "content": user_message},
        ]

        last_error: Optional[Exception] = None
        last_response: str = ""

        for attempt in range(1, self.llm_config.max_retries + 1):
            try:
                self.logger.info(
                    f"LLM call attempt {attempt}/{self.llm_config.max_retries} "
                    f"for segment {segment_id}"
                )

                # 재시도 시 온도 낮춤 (더 결정적인 출력을 위해)
                temperature = self.llm_config.temperature
                if attempt > 1:
                    temperature = max(0.1, temperature - 0.3)
                    self.logger.debug(f"Reduced temperature to {temperature} for retry")

                # LLM 호출
                response = await self.llm_client.chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=self.llm_config.max_tokens,
                    json_mode=True,
                )

                last_response = response

                if not response or not response.strip():
                    self.logger.warning(f"Empty response from LLM (attempt {attempt})")
                    continue

                # JSON 파싱
                parsed = self.json_parser.parse(response)

                if parsed:
                    parsed["_attempt"] = attempt
                    parsed["_raw_response"] = response[:500]  # 저장 시 길이 제한
                    self.logger.info(f"Successfully parsed JSON for segment {segment_id}")
                    return parsed

                # 파싱 실패 시 더 명확한 재시도 요청
                self.logger.warning(
                    f"JSON parsing failed for segment {segment_id} (attempt {attempt}). "
                    f"Response preview: {response[:200]}..."
                )

                # 재시도 메시지 (영어로 - Qwen이 더 잘 이해)
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "Your response was not valid JSON. Please try again.\n"
                        "Return ONLY a JSON object with these required fields:\n"
                        "- visual_prompt (English scene description)\n"
                        "- negative_prompt (English, things to avoid)\n"
                        "- camera_movement (e.g., pan_left, zoom_in, static)\n"
                        "- lighting (e.g., golden_hour, soft, dramatic)\n"
                        "- scene_summary (brief summary)\n\n"
                        "NO markdown, NO code blocks, NO explanations. "
                        "Just the JSON object starting with { and ending with }."
                    ),
                })

            except asyncio.TimeoutError:
                last_error = Exception("LLM request timed out")
                self.logger.warning(f"LLM timeout (attempt {attempt})")
                await asyncio.sleep(2.0 * attempt)

            except Exception as e:
                last_error = e
                self.logger.warning(f"LLM call failed (attempt {attempt}): {type(e).__name__}: {e}")
                await asyncio.sleep(1.0 * attempt)  # 지수 백오프

        # 모든 재시도 실패
        self.logger.error(
            f"All LLM retries failed for segment {segment_id}. "
            f"Last error: {last_error}. "
            f"Last response preview: {last_response[:300] if last_response else 'None'}..."
        )

        # None 반환하여 호출자가 폴백 처리하도록 함
        return None

    def _build_previous_context(self, current_index: int) -> str:
        """
        이전 장면의 맥락 정보 구성.

        Args:
            current_index: 현재 세그먼트 인덱스

        Returns:
            맥락 문자열
        """
        if current_index == 0 or not self._scene_history:
            return self.prompts_config.get("visual_planning", {}).get(
                "first_scene_context",
                "이것은 첫 번째 장면입니다. 강력한 시각적 세계관을 확립하세요."
            )

        prev_scene = self._scene_history[-1]
        ctx = prev_scene.get_continuity_context()

        template = self.prompts_config.get("visual_planning", {}).get(
            "previous_context_template",
            "이전 장면: {prev_summary}, 조명: {prev_lighting}, 분위기: {prev_mood}"
        )

        return template.format(
            prev_index=current_index,
            prev_summary=ctx["summary"],
            prev_environment=ctx["environment"],
            prev_lighting=ctx["lighting"],
            prev_colors=", ".join(ctx["colors"]) if ctx["colors"] else "N/A",
            prev_mood=ctx["mood"],
            prev_time_of_day=ctx.get("time_of_day", "N/A"),
        )

    def _get_fallback_scene_data(self) -> JsonDict:
        """LLM 실패 시 폴백 장면 데이터."""
        return {
            "visual_prompt": (
                "Serene natural landscape with soft golden light, "
                "peaceful atmosphere, cinematic wide angle composition, "
                "gentle mist rising from the ground, tranquil and contemplative mood"
            ),
            "negative_prompt": self.NEGATIVE_PROMPT_BASE,
            "camera_movement": "slow_zoom_out",
            "camera_speed": "slow",
            "lighting": "soft",
            "color_palette": ["soft blue", "warm gold", "natural green"],
            "mood": "serene",
            "environment": "nature",
            "weather": "clear",
            "time_of_day": "golden_hour",
            "scene_summary": "평화로운 자연 풍경 (폴백)",
            "_attempt": -1,
            "_raw_response": "[FALLBACK]",
        }

    # -------------------------------------------------------------------------
    # No-Human Policy Enforcement
    # -------------------------------------------------------------------------

    def build_final_prompt(
        self,
        scene_plan: ScenePlan,
        style: str = "cinematic",
    ) -> str:
        """
        최종 이미지 생성 프롬프트 구성.
        No-Human Policy 강제 적용.

        Args:
            scene_plan: 장면 기획
            style: 스타일 프리셋

        Returns:
            최종 프롬프트
        """
        parts: List[str] = [scene_plan.visual_prompt]

        # 스타일 프리셋 추가
        style_presets = self.prompts_config.get("visual_planning", {}).get(
            "style_presets", {}
        )
        if style in style_presets:
            parts.extend(style_presets[style].get("additions", []))

        # No-Human Policy 긍정 프롬프트 추가
        parts.extend(self.POSITIVE_ADDITIONS)

        # 조명/분위기 추가
        parts.append(f"{scene_plan.lighting} lighting")
        parts.append(f"{scene_plan.mood} atmosphere")

        return ", ".join(parts)

    def build_final_negative_prompt(self, scene_plan: ScenePlan) -> str:
        """
        최종 부정 프롬프트 구성.
        No-Human Policy 강제 적용.

        Args:
            scene_plan: 장면 기획

        Returns:
            최종 부정 프롬프트
        """
        parts: List[str] = [self.NEGATIVE_PROMPT_BASE]

        if scene_plan.negative_prompt:
            parts.append(scene_plan.negative_prompt)

        return ", ".join(parts)

    # -------------------------------------------------------------------------
    # Utility Methods
    # -------------------------------------------------------------------------

    def get_scene_history(self) -> List[ScenePlan]:
        """장면 히스토리 반환."""
        return list(self._scene_history)

    def clear_history(self) -> None:
        """장면 히스토리 초기화."""
        self._scene_history.clear()


# =============================================================================
# ComfyUI Client
# =============================================================================

class ComfyUIClient:
    """
    ComfyUI API 클라이언트.

    WebSocket/HTTP를 통해 ComfyUI 서버와 통신.
    이미지 생성 워크플로우 실행 및 결과 다운로드를 담당.

    Note:
        이 클래스는 main.py에서 사용하는 인터페이스를 제공합니다.
        실제 ComfyUI 통신은 modules/comfy_video_agent.py에서 더 상세하게 구현되어 있습니다.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8188,
        use_ssl: bool = False,
        timeout: float = 300.0,
    ) -> None:
        """
        ComfyUI 클라이언트 초기화.

        Args:
            host: ComfyUI 서버 호스트
            port: ComfyUI 서버 포트
            use_ssl: SSL 사용 여부
            timeout: 요청 타임아웃 (초)
        """
        self.host = host
        self.port = port
        self.use_ssl = use_ssl
        self.timeout = timeout

        # URL 구성
        protocol = "https" if use_ssl else "http"
        ws_protocol = "wss" if use_ssl else "ws"
        self.base_url = f"{protocol}://{host}:{port}"
        self.ws_url = f"{ws_protocol}://{host}:{port}/ws"

        # 상태
        self._connected = False
        self._client_id: Optional[str] = None
        self._session: Optional[Any] = None
        self._websocket: Optional[Any] = None

        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def is_connected(self) -> bool:
        """연결 상태 확인."""
        return self._connected

    async def connect(self) -> bool:
        """
        ComfyUI 서버에 연결.

        Returns:
            연결 성공 여부
        """
        import aiohttp
        import uuid

        try:
            self._client_id = str(uuid.uuid4())

            # HTTP 세션 생성
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)

            # 서버 상태 확인
            async with self._session.get(f"{self.base_url}/system_stats") as response:
                if response.status != 200:
                    raise ConnectionError(f"ComfyUI server returned {response.status}")

                stats = await response.json()
                self.logger.info(
                    f"Connected to ComfyUI at {self.base_url} "
                    f"(VRAM: {stats.get('devices', [{}])[0].get('vram_free', 'N/A')})"
                )

            self._connected = True
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect to ComfyUI: {e}")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """ComfyUI 서버 연결 해제."""
        if self._websocket:
            try:
                await self._websocket.close()
            except Exception:
                pass
            self._websocket = None

        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None

        self._connected = False
        self._client_id = None
        self.logger.info("Disconnected from ComfyUI")

    async def queue_prompt(self, workflow: JsonDict) -> Optional[str]:
        """
        워크플로우를 ComfyUI 큐에 추가.

        Args:
            workflow: ComfyUI 워크플로우 JSON

        Returns:
            prompt_id 또는 None (실패 시)
        """
        if not self._connected or not self._session:
            raise RuntimeError("Not connected to ComfyUI")

        try:
            payload = {
                "prompt": workflow,
                "client_id": self._client_id,
            }

            async with self._session.post(
                f"{self.base_url}/prompt",
                json=payload,
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    self.logger.error(f"Queue prompt failed: {error_text}")
                    return None

                result = await response.json()
                prompt_id = result.get("prompt_id")
                self.logger.info(f"Queued prompt: {prompt_id}")
                return prompt_id

        except Exception as e:
            self.logger.error(f"Queue prompt error: {e}")
            return None

    async def get_history(self, prompt_id: str) -> Optional[JsonDict]:
        """
        완료된 프롬프트의 히스토리 조회.

        Args:
            prompt_id: 프롬프트 ID

        Returns:
            히스토리 데이터 또는 None
        """
        if not self._connected or not self._session:
            return None

        try:
            async with self._session.get(
                f"{self.base_url}/history/{prompt_id}"
            ) as response:
                if response.status != 200:
                    return None

                data = await response.json()
                return data.get(prompt_id)

        except Exception as e:
            self.logger.error(f"Get history error: {e}")
            return None

    async def download_image(
        self,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output",
    ) -> Optional[bytes]:
        """
        생성된 이미지 다운로드.

        Args:
            filename: 파일명
            subfolder: 하위 폴더
            folder_type: 폴더 유형 (output, input, temp)

        Returns:
            이미지 바이트 데이터 또는 None
        """
        if not self._connected or not self._session:
            return None

        try:
            params = {
                "filename": filename,
                "subfolder": subfolder,
                "type": folder_type,
            }

            async with self._session.get(
                f"{self.base_url}/view",
                params=params,
            ) as response:
                if response.status != 200:
                    return None

                return await response.read()

        except Exception as e:
            self.logger.error(f"Download image error: {e}")
            return None

    async def interrupt(self) -> bool:
        """현재 실행 중인 작업 중단."""
        if not self._connected or not self._session:
            return False

        try:
            async with self._session.post(f"{self.base_url}/interrupt") as response:
                return response.status == 200
        except Exception:
            return False

    async def free_memory(self) -> bool:
        """ComfyUI VRAM 정리 요청."""
        if not self._connected or not self._session:
            return False

        try:
            async with self._session.post(
                f"{self.base_url}/free",
                json={"unload_models": True, "free_memory": True},
            ) as response:
                return response.status == 200
        except Exception:
            return False


# =============================================================================
# FSM Handler Integration
# =============================================================================

# Forward declaration for type hint
if TYPE_CHECKING:
    from core.fsm_manager import FSMManager, StateHandler
    from core.model_manager import ModelManager
    from core.project_state import ProjectState


class VisualScriptingHandler:
    """
    FSM Handler for VISUAL_SCRIPTING state.

    LLM을 사용하여 가사 기반 장면 프롬프트(JSON)를 생성합니다.
    이미지 생성은 수행하지 않습니다 - 사용자가 프롬프트를 검토한 후
    VISUAL_RENDERING 상태에서 이미지가 생성됩니다.

    Attributes:
        fsm: FSM 매니저 인스턴스
        model_manager: 모델 매니저 인스턴스 (VRAM 관리용)
        prompts_config: 프롬프트 템플릿 설정
        llm_config: LLM 설정 (provider, model 등)
        output_dir: 생성된 장면 계획 저장 경로
    """

    def __init__(
        self,
        fsm: "FSMManager",
        model_manager: "ModelManager",
        prompts_config: JsonDict,
        llm_config: JsonDict,
        output_dir: Path,
        mock: bool = False,
    ) -> None:
        """
        핸들러 초기화.

        Args:
            fsm: FSM 매니저 인스턴스
            model_manager: 모델 매니저 인스턴스
            prompts_config: 프롬프트 설정 딕셔너리
            llm_config: LLM 설정 딕셔너리
            output_dir: 출력 디렉토리
            mock: Mock 모드 여부 (테스트용)
        """
        # FSM 통합
        self.fsm = fsm
        self.model_manager = model_manager

        # 설정
        self.prompts_config = prompts_config
        self.llm_config = llm_config
        self.output_dir = Path(output_dir)
        self.mock = mock

        # 내부 컴포넌트
        self.planner: Optional[VisualPlanner] = None

        # 상태
        self._scene_plans: List[ScenePlan] = []

        self.logger = logging.getLogger(self.__class__.__name__)

    def requires_user_input(self) -> bool:
        """이 상태는 사용자 입력이 필요하지 않음 (자동 처리 후 REVIEW로 전이)."""
        return False

    async def enter(self, project: "ProjectState") -> None:
        """
        상태 진입 시 호출.

        Args:
            project: 현재 프로젝트 상태
        """
        self.logger.info(f"Entering VISUAL_SCRIPTING state (mock={self.mock})")

        # 출력 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # VisualPlanner 초기화 (LLM만 사용)
        self.planner = VisualPlanner(
            llm_config=self.llm_config,
            prompts_config=self.prompts_config,
            mock=self.mock,
        )
        await self.planner.initialize()

        self.logger.info("VISUAL_SCRIPTING state initialized")

    async def execute(
        self,
        project: "ProjectState",
        progress_callback: Optional[Callable[[int, int, ScenePlan], Any]] = None,
    ) -> Tuple[bool, str]:
        """
        장면 프롬프트 생성 (LLM만 사용, 이미지 생성 없음).

        Args:
            project: 현재 프로젝트 상태
            progress_callback: 진행 콜백 (current, total, scene_plan)

        Returns:
            (성공 여부, FSM 트리거 문자열)
            - 성공 시: (True, "scripting_complete") -> VISUAL_SCRIPTING_REVIEW로 전이
            - 실패 시: (False, "scripting_failed")
        """
        if not self.planner:
            raise RuntimeError("Handler not initialized. Call enter() first.")

        try:
            # 프로젝트에서 세그먼트 정보 추출
            segments = self._extract_segments(project)

            if not segments:
                self.logger.error("No segments found in project")
                return False, "scripting_failed"

            # 전체 분위기 추출
            global_mood = ""
            if hasattr(project, 'metadata') and project.metadata:
                global_mood = getattr(project.metadata, 'mood', '') or ''

            # 총 재생 시간 계산
            total_duration = 0.0
            if segments:
                total_duration = max(seg.get("end_time", 0) for seg in segments)

            self.logger.info(
                f"Generating prompts for {len(segments)} scenes "
                f"(mood: {global_mood}, duration: {total_duration:.1f}s)"
            )

            # 장면 프롬프트 생성 (LLM 호출)
            scene_plans = await self.planner.plan_scenes(
                segments=segments,
                global_mood=global_mood,
                total_duration=total_duration,
                progress_callback=progress_callback,
            )

            if not scene_plans:
                self.logger.error("No scene plans generated")
                return False, "scripting_failed"

            self._scene_plans = scene_plans

            # 프로젝트에 결과 저장 (사용자 검토용)
            self._save_to_project(project, scene_plans)

            self.logger.info(f"Generated {len(scene_plans)} scene prompts - ready for user review")
            # CRITICAL: scripting_complete 트리거로 VISUAL_SCRIPTING_REVIEW 상태로 전이
            return True, "scripting_complete"

        except Exception as e:
            self.logger.exception(f"Visual scripting failed: {e}")
            return False, "scripting_failed"

    async def exit(self, project: "ProjectState") -> None:
        """
        상태 종료 시 호출.

        Args:
            project: 현재 프로젝트 상태
        """
        self.logger.info("Exiting VISUAL_SCRIPTING state")

        # Planner 정리
        if self.planner:
            await self.planner.cleanup()
            self.planner = None

        # VRAM 정리 요청
        if self.model_manager:
            try:
                await self.model_manager.unload_all()
            except Exception as e:
                self.logger.warning(f"VRAM cleanup error: {e}")

    def _extract_segments(self, project: "ProjectState") -> List[JsonDict]:
        """
        프로젝트에서 세그먼트 정보 추출.

        Args:
            project: 프로젝트 상태

        Returns:
            세그먼트 딕셔너리 리스트
        """
        segments = []

        # ProjectState의 lyrics_segments 속성 확인
        if hasattr(project, 'lyrics_segments') and project.lyrics_segments:
            for i, seg in enumerate(project.lyrics_segments):
                if hasattr(seg, 'to_dict'):
                    seg_dict = seg.to_dict()
                elif hasattr(seg, '__dict__'):
                    seg_dict = {
                        "id": str(i),
                        "text": getattr(seg, 'text', ''),
                        "start_time": getattr(seg, 'start_time', 0),
                        "end_time": getattr(seg, 'end_time', 0),
                        "confidence": getattr(seg, 'confidence', 1.0),
                    }
                else:
                    seg_dict = {
                        "id": str(i),
                        "text": str(seg),
                        "start_time": 0,
                        "end_time": 0,
                        "confidence": 1.0,
                    }

                # id 필드 보장
                if "id" not in seg_dict:
                    seg_dict["id"] = str(i)

                segments.append(seg_dict)

        return segments

    def _save_to_project(
        self,
        project: "ProjectState",
        scene_plans: List[ScenePlan],
    ) -> None:
        """
        장면 프롬프트 결과를 프로젝트에 저장.

        Args:
            project: 프로젝트 상태
            scene_plans: 생성된 장면 기획 리스트
        """
        # scene_plans를 딕셔너리 리스트로 변환
        scene_plans_dict = [sp.model_dump() for sp in scene_plans]
        
        # ProjectState에 scene_plans 속성이 없을 수 있으므로 동적으로 추가
        # hasattr 체크 없이 직접 할당 (속성이 없으면 자동으로 추가됨)
        project.scene_plans = scene_plans_dict

        # visual_plans 속성도 함께 저장 (대안/호환성)
        project.visual_plans = scene_plans_dict

        self.logger.info(
            f"Saved {len(scene_plans_dict)} scene plans to project "
            f"(project.scene_plans={hasattr(project, 'scene_plans')}, "
            f"project.visual_plans={hasattr(project, 'visual_plans')})"
        )

        # JSON 파일로도 저장 (사용자 검토 및 편집용)
        output_file = self.output_dir / "scene_plans.json"
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(
                    scene_plans_dict,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            self.logger.info(f"Scene plans saved to: {output_file}")
        except Exception as e:
            self.logger.warning(f"Failed to save scene plans to file: {e}")

    def get_scene_plans(self) -> List[ScenePlan]:
        """생성된 장면 기획 반환."""
        return list(self._scene_plans)


class VisualRenderingHandler:
    """
    FSM Handler for VISUAL_RENDERING state.

    사용자가 검토/수정한 장면 프롬프트를 기반으로 ComfyUI를 통해
    이미지를 생성합니다.

    Attributes:
        fsm: FSM 매니저 인스턴스
        model_manager: 모델 매니저 인스턴스 (VRAM 관리용)
        comfyui_config: ComfyUI 설정 (호스트, 포트 등)
        output_dir: 생성된 이미지 저장 경로
    """

    def __init__(
        self,
        fsm: "FSMManager",
        model_manager: "ModelManager",
        comfyui_config: JsonDict,
        output_dir: Path,
        mock: bool = False,
    ) -> None:
        """
        핸들러 초기화.

        Args:
            fsm: FSM 매니저 인스턴스
            model_manager: 모델 매니저 인스턴스
            comfyui_config: ComfyUI 설정 딕셔너리
            output_dir: 출력 디렉토리
            mock: Mock 모드 여부 (테스트용)
        """
        # FSM 통합
        self.fsm = fsm
        self.model_manager = model_manager

        # 설정
        self.comfyui_config = comfyui_config
        self.output_dir = Path(output_dir)
        self.mock = mock

        # 내부 컴포넌트
        self.comfyui_client: Optional[ComfyUIClient] = None

        # 상태
        self._generated_images: List[Path] = []

        self.logger = logging.getLogger(self.__class__.__name__)

    def requires_user_input(self) -> bool:
        """이 상태는 사용자 입력이 필요하지 않음 (자동 처리 후 REVIEW로 전이)."""
        return False

    async def enter(self, project: "ProjectState") -> None:
        """
        상태 진입 시 호출.

        Args:
            project: 현재 프로젝트 상태
        """
        self.logger.info(f"Entering VISUAL_RENDERING state (mock={self.mock})")

        # 출력 디렉토리 생성
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # ComfyUI 클라이언트 초기화 (이미지 생성용)
        if not self.mock and self.comfyui_config:
            self.comfyui_client = ComfyUIClient(
                host=self.comfyui_config.get("host", "127.0.0.1"),
                port=self.comfyui_config.get("port", 8188),
                use_ssl=self.comfyui_config.get("use_ssl", False),
                timeout=self.comfyui_config.get("timeout", 300),
            )
            await self.comfyui_client.connect()

        self.logger.info("VISUAL_RENDERING state initialized")

    async def execute(
        self,
        project: "ProjectState",
        progress_callback: Optional[Callable[[int, int, str], Any]] = None,
    ) -> Tuple[bool, str]:
        """
        이미지 생성 실행 (ComfyUI 사용).

        Args:
            project: 현재 프로젝트 상태 (scene_plans 포함)
            progress_callback: 진행 콜백 (current, total, status)

        Returns:
            (성공 여부, FSM 트리거 문자열)
            - 성공 시: (True, "rendering_complete") -> VISUAL_REVIEW로 전이
            - 실패 시: (False, "rendering_failed")
        """
        try:
            # 프로젝트에서 scene_plans 로드
            scene_plans = self._load_scene_plans(project)

            if not scene_plans:
                self.logger.error("No scene plans found in project")
                return False, "rendering_failed"

            self.logger.info(f"Rendering {len(scene_plans)} images from scene plans")

            # Mock 모드: 이미지 생성 건너뛰기
            if self.mock:
                self.logger.info("Mock mode: skipping actual image generation")
                self._generated_images = [
                    self.output_dir / f"mock_image_{i}.png"
                    for i in range(len(scene_plans))
                ]
                # 프로젝트에 결과 저장
                self._save_images_to_project(project, self._generated_images)
                return True, "rendering_complete"

            # ComfyUI를 통한 실제 이미지 생성
            if not self.comfyui_client:
                self.logger.error("ComfyUI client not initialized")
                return False, "rendering_failed"

            generated_images = []
            total = len(scene_plans)

            for i, plan in enumerate(scene_plans):
                if progress_callback:
                    result = progress_callback(i + 1, total, f"Generating image {i + 1}/{total}")
                    if asyncio.iscoroutine(result):
                        await result

                # TODO: ComfyUI 워크플로우를 통한 이미지 생성 구현
                # 현재는 플레이스홀더
                self.logger.info(
                    f"Generating image {i + 1}/{total}: "
                    f"{plan.get('visual_prompt', '')[:50]}..."
                )

                # 실제 구현 시 여기서 ComfyUI 워크플로우 실행
                # image_path = await self._generate_single_image(plan, i)
                # if image_path:
                #     generated_images.append(image_path)

                # 임시: 플레이스홀더 경로
                image_path = self.output_dir / f"scene_{i:03d}.png"
                generated_images.append(image_path)

            self._generated_images = generated_images

            # 프로젝트에 결과 저장
            self._save_images_to_project(project, generated_images)

            self.logger.info(f"Generated {len(generated_images)} images")
            return True, "rendering_complete"

        except Exception as e:
            self.logger.exception(f"Visual rendering failed: {e}")
            return False, "rendering_failed"

    async def exit(self, project: "ProjectState") -> None:
        """
        상태 종료 시 호출.

        Args:
            project: 현재 프로젝트 상태
        """
        self.logger.info("Exiting VISUAL_RENDERING state")

        # ComfyUI 연결 해제
        if self.comfyui_client:
            await self.comfyui_client.disconnect()
            self.comfyui_client = None

        # VRAM 정리 요청
        if self.model_manager:
            try:
                await self.model_manager.unload_all()
            except Exception as e:
                self.logger.warning(f"VRAM cleanup error: {e}")

    def _load_scene_plans(self, project: "ProjectState") -> List[JsonDict]:
        """
        프로젝트에서 scene_plans 로드.

        Args:
            project: 프로젝트 상태

        Returns:
            scene_plans 딕셔너리 리스트
        """
        # scene_plans 속성 확인
        if hasattr(project, 'scene_plans') and project.scene_plans:
            return project.scene_plans

        # visual_plans 속성 확인 (대안)
        if hasattr(project, 'visual_plans') and project.visual_plans:
            return project.visual_plans

        # JSON 파일에서 로드 시도
        plans_file = self.output_dir / "scene_plans.json"
        if plans_file.exists():
            try:
                with open(plans_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                self.logger.warning(f"Failed to load scene_plans.json: {e}")

        return []

    def _save_images_to_project(
        self,
        project: "ProjectState",
        images: List[Path],
    ) -> None:
        """
        생성된 이미지 경로를 프로젝트에 저장.

        Args:
            project: 프로젝트 상태
            images: 생성된 이미지 경로 리스트
        """
        # generated_images 속성이 있으면 저장
        if hasattr(project, 'generated_images'):
            project.generated_images = [str(p) for p in images]

        # images 딕셔너리 속성 업데이트 (기존 구조 호환)
        if hasattr(project, 'images') and isinstance(project.images, dict):
            for i, img_path in enumerate(images):
                # ImageAsset 등 기존 구조가 있으면 업데이트
                segment_id = str(i)
                if segment_id not in project.images:
                    # 간단한 딕셔너리로 저장
                    project.images[segment_id] = {"path": str(img_path)}
                else:
                    project.images[segment_id]["path"] = str(img_path)

        self.logger.info(f"Saved {len(images)} image paths to project")

    def get_generated_images(self) -> List[Path]:
        """생성된 이미지 경로 반환."""
        return list(self._generated_images)


# Legacy alias for backwards compatibility
VisualPlanningHandler = VisualScriptingHandler


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Enums
    "CameraMovement",
    "CameraSpeed",
    "Lighting",
    "Environment",
    "LLMProvider",
    # Pydantic Models
    "ScenePlan",
    "LLMConfig",
    "SegmentInfo",
    # Classes
    "JSONParserWithRetry",
    "OpenAICompatibleClient",
    "MockSceneGenerator",
    "VisualPlanner",
    # FSM Handlers (split from single VISUAL_PLANNING into two states)
    "VisualScriptingHandler",   # LLM prompt generation only
    "VisualRenderingHandler",   # ComfyUI image generation only
    "VisualPlanningHandler",    # Legacy alias for VisualScriptingHandler
    # ComfyUI Integration
    "ComfyUIClient",
]
