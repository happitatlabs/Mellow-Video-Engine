"""
Tool Registry: LLM 에이전트가 사용할 도구를 등록·검색·실행하는 중앙 저장소.

설계 원칙:
  - @tool 데코레이터로 함수를 등록하면 LLM이 선택 가능한 도구가 된다.
  - get_tools_prompt()로 LLM 시스템 프롬프트에 도구 목록을 주입한다.
  - execute()로 LLM이 선택한 도구를 이름 + 인자로 실행한다.
"""

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from typing import Callable, Dict, Any, List, Optional, Set

# Observation 직전 디버그 로그: 터미널에 출력할지 (ML_TOOL_DEBUG=1 또는 로거 레벨 DEBUG)
def _observation_debug_enabled() -> bool:
    if __import__("os").environ.get("ML_TOOL_DEBUG", "").strip().lower() in ("1", "true", "yes"):
        return True
    return logger.isEnabledFor(logging.DEBUG)

logger = logging.getLogger(__name__)


def _observation_to_string(raw: Any) -> str:
    """도구 반환값을 Observation 문자열로 안전하게 변환. None/비문자열/직렬화 실패 시 명시적 메시지."""
    if raw is None:
        return "[Empty] 도구 반환값이 없습니다."
    if isinstance(raw, str):
        return raw
    try:
        return str(raw)
    except Exception as e:
        return f"[Serialization] str(result) 실패: {e}"


def _observation_debug_log(tool_name: str, raw_result: Any, observation_str: str) -> None:
    """Observation으로 전달되기 직전 값을 터미널에 출력 (Debug Logger)."""
    if not _observation_debug_enabled():
        return
    typ = type(raw_result).__name__
    repr_preview = repr(raw_result)[:400] if raw_result is not None else "None"
    obs_preview = (observation_str[:500] + "..." if len(observation_str) > 500 else observation_str)
    line = (
        f"[Observation Debug] tool={tool_name} | type={typ} | repr={repr_preview!r} | "
        f"observation_len={len(observation_str)} | observation_preview={obs_preview!r}"
    )
    try:
        print(line, flush=True)
    except Exception:
        pass
    logger.debug("%s", line)


try:
    # Optional dependency: used only for selective exception propagation.
    # Avoids circular imports while letting security violations "hard stop".
    from mellow_link.core.security_manager import SecurityBlocked  # type: ignore
except Exception:  # pragma: no cover
    SecurityBlocked = None  # type: ignore


@dataclass
class Tool:
    """등록된 도구 하나의 메타데이터."""
    name: str
    func: Callable
    description: str
    parameters: Dict[str, Dict[str, str]]  # {param_name: {"type": ..., "default": ...}}
    category: str = "general"


class ToolRegistry:
    """도구 등록·검색·실행을 담당하는 중앙 레지스트리."""
    _MAX_DESCRIPTION_CHARS = 280

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        # 같은 callable이 다른 이름으로 중복 등록되는 케이스 방지
        self._tool_fingerprints: Set[str] = set()
        self._frozen: bool = False

    # ──────────────────────────────────────────
    # Freeze / Integrity
    # ──────────────────────────────────────────

    def freeze(self) -> None:
        """
        레지스트리를 봉인한다.

        한 번 freeze()된 이후에는 register()를 통해 새 도구를 추가할 수 없다.
        (런타임 도구 주입 방지)
        """
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        """레지스트리 봉인 여부."""
        return self._frozen

    # ──────────────────────────────────────────
    # 등록
    # ──────────────────────────────────────────

    def register(
        self,
        func: Optional[Callable] = None,
        *,
        category: str = "general",
    ) -> Callable:
        """
        도구 등록 데코레이터. 두 가지 방식으로 사용 가능:

            @tool
            def my_func(...): ...

            @tool(category="memory")
            def my_func(...): ...
        """
        def _do_register(fn: Callable) -> Callable:
            if self._frozen:
                raise RuntimeError(
                    f"ToolRegistry is frozen; cannot register new tool: {getattr(fn, '__name__', '<unknown>')}"
                )

            name = fn.__name__
            doc = inspect.getdoc(fn) or "설명 없음."
            fingerprint = f"{getattr(fn, '__module__', '')}:{getattr(fn, '__qualname__', name)}"

            # 중복 등록 방지 (이름 기준)
            if name in self._tools:
                existing = self._tools[name]
                logger.warning(
                    "[ToolRegistry] duplicate tool rejected by name: %s (existing=%s:%s, incoming=%s)",
                    name,
                    getattr(existing.func, "__module__", ""),
                    getattr(existing.func, "__qualname__", existing.name),
                    fingerprint,
                )
                return fn

            # 중복 등록 방지 (callable identity 기준)
            if fingerprint in self._tool_fingerprints:
                logger.warning(
                    "[ToolRegistry] duplicate tool rejected by callable identity: %s (%s)",
                    name,
                    fingerprint,
                )
                return fn

            # 파라미터 분석 (type hint + default)
            sig = inspect.signature(fn)
            params: Dict[str, Dict[str, str]] = {}
            for pname, param in sig.parameters.items():
                if pname == "self":
                    continue
                ptype = (
                    param.annotation.__name__
                    if hasattr(param.annotation, "__name__")
                    else str(param.annotation)
                    if param.annotation != inspect.Parameter.empty
                    else "string"
                )
                pinfo: Dict[str, str] = {"type": ptype}
                if param.default is not inspect.Parameter.empty:
                    pinfo["default"] = str(param.default)
                else:
                    pinfo["required"] = "true"
                params[pname] = pinfo

            tool_obj = Tool(
                name=name,
                func=fn,
                description=doc,
                parameters=params,
                category=category,
            )
            self._tools[name] = tool_obj
            self._tool_fingerprints.add(fingerprint)
            logger.info("[ToolRegistry] registered(new): %s (%s)", name, category)
            return fn

        # @tool  (인자 없이 사용)
        if func is not None:
            return _do_register(func)
        # @tool(category="...")  (인자와 함께 사용)
        return _do_register

    # 명시적 API 별칭: 외부에서 register_tool 호출 시 호환
    def register_tool(
        self,
        func: Optional[Callable] = None,
        *,
        category: str = "general",
    ) -> Callable:
        """register()의 호환 별칭."""
        return self.register(func=func, category=category)

    # ──────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────

    async def execute(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> str:
        """
        LLM이 선택한 도구를 실행하고 결과를 문자열로 반환.

        Args:
            tool_name: 실행할 도구 이름.
            args: 도구에 전달할 인자 딕셔너리.

        Returns:
            실행 결과 문자열. 에러 시에도 문자열로 반환 (LLM이 읽을 수 있도록).
        """
        if tool_name not in self._tools:
            available = ", ".join(sorted(self._tools.keys()))
            return f"[Error] '{tool_name}' 도구를 찾을 수 없습니다. 사용 가능: {available}"

        tool = self._tools[tool_name]
        safe_args = args or {}

        # 등록된 파라미터에 없는 인자 제거 (LLM이 엉뚱한 키를 보낼 수 있음)
        allowed_params = set(tool.parameters.keys())
        filtered_args = {k: v for k, v in safe_args.items() if k in allowed_params}

        # 필수 인자 누락 시 무의미한 복구 방지: 필터 후 비어있으면 필수 인자 여부 확인
        required_params = [
            p for p, info in tool.parameters.items()
            if info.get("required") == "true"
        ]
        if not filtered_args and required_params:
            return (
                f"[Error] {tool_name} 필수 인자 누락: {', '.join(required_params)}\n"
                f"⚠️ args 객체가 비어있습니다. JSON 형식: {{\"tool\":\"{tool_name}\",\"args\":{{\"필수인자\":\"값\"}}}}"
            )
        missing_required = [p for p in required_params if p not in filtered_args]
        if missing_required:
            return (
                f"[Error] {tool_name} 필수 인자 누락: {', '.join(missing_required)}\n"
                f"제공된 인자: {list(filtered_args.keys())}\n"
                f"필수 인자 예시: {{\"tool\":\"{tool_name}\",\"args\":{{\"{missing_required[0]}\":\"값\"}}}}"
            )

        try:
            logger.info("[Execute] %s(%s)", tool_name, filtered_args)

            if inspect.iscoroutinefunction(tool.func):
                result = await tool.func(**filtered_args)
            else:
                result = tool.func(**filtered_args)

            # 동기 함수가 실수로 코루틴 객체를 반환한 경우(await 누락) 복구 및 경고
            if asyncio.iscoroutine(result):
                logger.error(
                    "[Execute] %s returned a coroutine (possible missing await in sync path). Awaiting to avoid data loss.",
                    tool_name,
                )
                result = await result

            observation_str = _observation_to_string(result)
            _observation_debug_log(tool_name, result, observation_str)
            return observation_str

        except TypeError as e:
            return f"[Error] {tool_name} 인자 오류: {e}"
        except Exception as e:
            # Security / Permission violations must HARD STOP (propagate)
            if isinstance(e, PermissionError):
                raise
            if SecurityBlocked is not None and isinstance(e, SecurityBlocked):
                raise
            logger.exception("[Execute] %s failed", tool_name)
            return f"[Error] {tool_name} 실행 실패: {e}"

    # ──────────────────────────────────────────
    # LLM 프롬프트 생성
    # ──────────────────────────────────────────

    def get_tools_prompt(self) -> str:
        """LLM 시스템 프롬프트에 삽입할 도구 목록 JSON."""
        tools_desc: List[Dict[str, Any]] = []
        for tool in self.get_all_tools():
            tools_desc.append({
                "name": tool.name,
                "category": tool.category,
                "description": self._compact_description(tool.description),
                "parameters": tool.parameters,
            })
        return json.dumps(tools_desc, indent=2, ensure_ascii=False)

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        OpenAI Function Calling 형식의 도구 스키마 반환.
        Ollama Native Tool Calling API에서 사용.
        
        Returns:
            OpenAI Function Calling 형식의 도구 스키마 리스트
        """
        tools_schema: List[Dict[str, Any]] = []
        
        for tool in self.get_all_tools():
            # Python 타입을 JSON Schema 타입으로 매핑
            def _convert_type(python_type: str) -> str:
                type_mapping = {
                    "str": "string",
                    "string": "string",
                    "int": "integer",
                    "integer": "integer",
                    "float": "number",
                    "number": "number",
                    "bool": "boolean",
                    "boolean": "boolean",
                    "list": "array",
                    "array": "array",
                    "dict": "object",
                    "object": "object",
                }
                return type_mapping.get(python_type.lower(), "string")
            
            # properties와 required 생성
            properties: Dict[str, Any] = {}
            required: List[str] = []
            
            for param_name, param_info in tool.parameters.items():
                param_type = param_info.get("type", "string")
                json_type = _convert_type(param_type)
                
                prop_schema: Dict[str, Any] = {
                    "type": json_type,
                    "description": f"Parameter: {param_name}"
                }
                
                # default 값이 있으면 추가
                if "default" in param_info:
                    prop_schema["default"] = param_info["default"]
                
                properties[param_name] = prop_schema
                
                # required 리스트에 추가 (default가 없으면 필수)
                if param_info.get("required") == "true" or "default" not in param_info:
                    required.append(param_name)
            
            # OpenAI Function Calling 형식으로 변환
            function_schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": self._compact_description(tool.description),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required if required else [],
                    }
                }
            }
            
            tools_schema.append(function_schema)
        
        return tools_schema

    def get_tool_names(self) -> List[str]:
        """등록된 도구 이름 목록."""
        return list(self._tools.keys())

    def get_all_tools(self) -> List[Tool]:
        """
        유니크 도구 리스트 반환 (이름 기준 정규화).
        외부 호출이 리스트를 기대할 때도 항상 중복 없는 결과를 보장한다.
        """
        unique: Dict[str, Tool] = {}
        for tool in self._tools.values():
            if tool.name not in unique:
                unique[tool.name] = tool
        return list(unique.values())

    def get_tool(self, name: str) -> Optional[Tool]:
        """이름으로 도구 메타데이터 조회."""
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def _compact_description(self, description: str) -> str:
        """프롬프트 토큰 절약을 위해 도구 설명 길이 상한 적용."""
        text = (description or "설명 없음.").strip()
        if len(text) <= self._MAX_DESCRIPTION_CHARS:
            return text
        return text[: self._MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."


# ──────────────────────────────────────────
# 전역 싱글턴
# ──────────────────────────────────────────
registry = ToolRegistry()
tool = registry.register
