"""
코드 품질 감리 P3: Magic Numbers 상수화.

agent_brain 및 관련 모듈에서 사용하는 숫자를 명명된 상수로 두어
가독성·유지보수성을 높입니다.
"""

# Observation / 프롬프트 크기 제한
MAX_OBSERVATION_SIZE = 1200
"""Observation 문자열 최대 문자 수 (프롬프트 블로트 방지)."""

LOG_TRUNCATE_LEN = 200
"""로그/요약 트런케이션 시 표시할 최대 문자 수."""

MAX_SYSTEM_PROMPT_CHARS = 50_000
"""시스템 프롬프트 길이 경고 임계값 (문자 수, 약 50KB)."""

MAX_TOOLS_SCHEMA_CHARS = 100_000
"""도구 스키마 JSON 크기 경고 임계값 (문자 수, 약 100KB)."""
