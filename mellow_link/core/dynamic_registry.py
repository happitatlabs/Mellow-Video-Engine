"""
Dynamic Tool Registry - 동적 도구 확장 레지스트리 (Phase 4 → Phase 5)

기존 ToolRegistry를 감싸서 custom_tools/ 폴더와 DB(VERIFIED)의 동적 도구를
실시간 hot-reload로 통합 제공합니다.

✅ verified: 동적 코드 로딩 (importlib)
✅ verified: ToolForge 연동 (검증 후 자동 등록)
✅ verified: Security Level 연동 (NORMAL/HARD)
"""

import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from mellow_link.core.tool_registry import ToolRegistry, Tool

logger = logging.getLogger(__name__)


def _normalize_tool_name(name: str) -> str:
    """연속 밑줄(__)을 단일(_)로 합쳐 파일/DB 이름 불일치로 인한 중복 방지."""
    if not name:
        return name
    while "__" in name:
        name = name.replace("__", "_")
    return name


# ═══════════════════════════════════════════════
# Dynamic Tool Registry
# ═══════════════════════════════

class DynamicToolRegistry:
    """
    기본 레지스트리 + 동적 도구(custom_tools/ 및 DB VERIFIED)를 통합 제공.
    
    - custom_tools/ 폴더 내 .py 파일을 importlib으로 hot-reload
    - ToolForge에서 검증 완료된 도구를 즉시 등록
    - execute / get_tools_prompt / get_tool_names 는 기본 + 동적 통합
    """

    def __init__(self, base_registry: ToolRegistry):
        """
        Args:
            base_registry: 기본 도구 레지스트리 (agent_tools 등)
        """
        self._base = base_registry
        self._dynamic_tools: Dict[str, Tool] = {}
        self._custom_tools_dir: Optional[Path] = None
        self._loaded_modules: Dict[str, Any] = {}  # module_name -> module (reload 시 갱신)
        self._forge_registered: Dict[str, str] = {}  # tool_name -> tool_id (ToolForge 통해 등록된 것)
        logger.info("[DynamicToolRegistry] Initialized (wrapping base registry)")

    def set_custom_tools_dir(self, path: Path) -> None:
        """custom_tools 디렉터리 설정 (호출 후 reload_custom_tools 사용)."""
        self._custom_tools_dir = Path(path)
        logger.info("[DynamicToolRegistry] custom_tools dir set: %s", self._custom_tools_dir)

    def reload_custom_tools(self) -> int:
        """
        custom_tools/ 폴더 내 .py 파일 + DB VERIFIED 도구를 동적으로 로드.
        Hot-reload: 서버 재시작 없이 새 도구 즉시 반영.
        
        Returns:
            로드된 동적 도구 개수
        """
        self._dynamic_tools.clear()
        self._loaded_modules.clear()

        if not self._custom_tools_dir or not self._custom_tools_dir.is_dir():
            logger.debug("[DynamicToolRegistry] custom_tools dir not set or missing, DB only")
            return self._reload_verified_from_db()

        count = 0
        # 1. custom_tools/ .py 파일 로드
        for path in sorted(self._custom_tools_dir.glob("*.py")):
            if path.name.startswith("_"):
                continue
            stem = path.stem
            try:
                tool = self._load_module_as_tool(path, stem)
                if tool:
                    self._dynamic_tools[tool.name] = tool
                    count += 1
                    logger.info("[DynamicToolRegistry] Loaded: %s from %s", tool.name, path.name)
            except Exception as e:
                logger.warning("[DynamicToolRegistry] Failed to load %s: %s", path.name, e)

        # 2. DB VERIFIED 도구 중 파일로 없는 것 추가 (영속성 확보)
        db_count = self._reload_verified_from_db()
        count += db_count

        return count

    def _reload_verified_from_db(self) -> int:
        """
        DB의 VERIFIED 동적 도구 중 아직 _dynamic_tools에 없는 것을 등록.
        custom_tools/ 파일이 없거나 삭제된 경우에도 DB에서 복원.
        
        Returns:
            추가 등록된 도구 개수
        """
        count = 0
        try:
            from mellow_link.infra.memory_database import get_memory_db
            db = get_memory_db()
            verified = db.get_dynamic_tools_by_status(status="VERIFIED", limit=50)
            for rec in verified:
                normalized = _normalize_tool_name(rec.tool_name)
                if normalized in self._dynamic_tools:
                    continue
                if self.register_dynamic_from_db(
                    rec.tool_name,
                    rec.code,
                    rec.description,
                    rec.parameters_json,
                ):
                    count += 1
        except Exception as e:
            logger.warning("[DynamicToolRegistry] reload_verified_from_db failed: %s", e)
        return count

    def _load_module_as_tool(self, path: Path, expected_func_name: str) -> Optional[Tool]:
        """
        단일 .py 파일을 로드하고, expected_func_name(파일명과 동일) 함수를 Tool로 반환.
        """
        module_name = f"custom_tools_{path.stem}_{id(path)}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning("[DynamicToolRegistry] Invalid spec for %s", path)
            return None

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            logger.warning("[DynamicToolRegistry] exec_module failed for %s: %s", path, e)
            raise

        func = getattr(module, expected_func_name, None)
        if not callable(func):
            logger.warning(
                "[DynamicToolRegistry] No callable '%s' in %s",
                expected_func_name, path.name
            )
            return None

        # 도구 이름: 실제 함수명을 정규화 (merge__x → merge_x, 로그/호출 일관성)
        raw_name = getattr(func, "__name__", expected_func_name)
        tool_name = _normalize_tool_name(raw_name)

        doc = inspect.getdoc(func) or "동적 도구 (설명 없음)"
        sig = inspect.signature(func)
        params: Dict[str, Dict[str, str]] = {}
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            ptype = (
                getattr(param.annotation, "__name__", str(param.annotation))
                if param.annotation != inspect.Parameter.empty
                else "string"
            )
            pinfo: Dict[str, str] = {"type": ptype}
            if param.default != inspect.Parameter.empty:
                pinfo["default"] = str(param.default)
            else:
                pinfo["required"] = "true"
            params[pname] = pinfo

        return Tool(
            name=tool_name,
            func=func,
            description=doc,
            parameters=params,
            category="dynamic",
        )

    def register_dynamic_from_db(self, name: str, code: str, description: str, parameters_json: str) -> bool:
        """
        DB에 저장된 VERIFIED 동적 도구를 런타임에 등록 (ToolForge로 검증된 코드 실행).
        주의: exec() 사용으로 제한된 샌드박스 수준. 호출 전 ToolForge 검증 완료된 코드만 사용.
        """
        try:
            from mellow_link.core.tool_forge import SAFE_BUILTINS
            restricted_globals = dict(SAFE_BUILTINS)
            restricted_globals["__builtins__"] = SAFE_BUILTINS
            # DB 저장 코드의 타입 힌트(List, Dict, Optional) 사용을 위해 제공
            from typing import List as _List, Dict as _Dict, Optional as _Optional
            restricted_globals["List"] = _List
            restricted_globals["Dict"] = _Dict
            restricted_globals["Optional"] = _Optional
            restricted_locals = {}
            exec(code, restricted_globals, restricted_locals)
            tool_name_normalized = _normalize_tool_name(name)
            func = restricted_locals.get(name) or restricted_locals.get(tool_name_normalized)
            if not callable(func):
                logger.warning("[DynamicToolRegistry] No callable '%s' in DB code", name)
                return False
            params = json.loads(parameters_json) if parameters_json else {}
            params_typed: Dict[str, Dict[str, str]] = {}
            for k, v in params.items() if isinstance(params, dict) else []:
                params_typed[k] = v if isinstance(v, dict) else {"type": "string"}
            tool = Tool(name=tool_name_normalized, func=func, description=description, parameters=params_typed, category="dynamic")
            self._dynamic_tools[tool_name_normalized] = tool
            logger.info("[DynamicToolRegistry] Registered dynamic tool from DB: %s", tool_name_normalized)
            return True
        except Exception as e:
            logger.exception("[DynamicToolRegistry] register_dynamic_from_db failed: %s", e)
            return False

    def register_from_forge(
        self,
        tool_name: str,
        file_path: Path,
        tool_id: str,
        description: str = "",
    ) -> bool:
        """
        ToolForge에서 검증 완료된 도구를 importlib으로 동적 로드하여 등록.
        ✅ verified: ToolForge → DynamicToolRegistry 연동 경로.

        Args:
            tool_name: 함수/도구 이름
            file_path: custom_tools/ 내 .py 파일 경로
            tool_id: ToolForge가 부여한 도구 ID
            description: 도구 설명

        Returns:
            등록 성공 여부
        """
        try:
            tool = self._load_module_as_tool(Path(file_path), tool_name)
            if tool is None:
                logger.warning(
                    "[DynamicToolRegistry] register_from_forge: "
                    "could not load '%s' from %s", tool_name, file_path,
                )
                return False
            if description:
                tool = Tool(
                    name=tool.name, func=tool.func,
                    description=description,
                    parameters=tool.parameters,
                    category="dynamic",
                )
            self._dynamic_tools[tool_name] = tool
            self._forge_registered[tool_name] = tool_id
            logger.info(
                "[DynamicToolRegistry] Registered from forge: %s (id=%s)",
                tool_name, tool_id,
            )
            return True
        except Exception as e:
            logger.exception(
                "[DynamicToolRegistry] register_from_forge failed: %s", e,
            )
            return False

    def get_forge_tools(self) -> Dict[str, str]:
        """ToolForge를 통해 등록된 도구 목록 {tool_name: tool_id}."""
        return dict(self._forge_registered)

    def get_tool_names(self) -> List[str]:
        """기본 + 동적 도구 이름 목록 (중복 없이)."""
        base_names = list(self._base.get_tool_names())
        dynamic_names = [n for n in self._dynamic_tools.keys() if n not in base_names]
        return base_names + dynamic_names

    def get_tools_prompt(self) -> str:
        """기본 + 동적 도구를 합친 JSON (LLM 프롬프트용)."""
        base_json = self._base.get_tools_prompt()
        base_list = json.loads(base_json) if base_json.strip() else []
        seen_names = {
            str(item.get("name", "")).strip()
            for item in base_list
            if isinstance(item, dict) and item.get("name")
        }
        for tool in self._dynamic_tools.values():
            # 중복 이름 도구는 스킵 (동일 카드 다중 주입 방지)
            if tool.name in seen_names:
                logger.debug(
                    "[DynamicToolRegistry] Skipping duplicate tool in prompt merge: %s",
                    tool.name,
                )
                continue
            base_list.append({
                "name": tool.name,
                "category": tool.category,
                "description": tool.description,
                "parameters": tool.parameters,
            })
            seen_names.add(tool.name)
        return json.dumps(base_list, indent=2, ensure_ascii=False)

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        """
        OpenAI Function Calling 형식의 도구 스키마 반환 (기본 + 동적 도구 통합).
        Ollama Native Tool Calling API에서 사용.
        
        Returns:
            OpenAI Function Calling 형식의 도구 스키마 리스트
        """
        # 기본 레지스트리의 스키마 가져오기
        base_schema = self._base.get_tools_schema()
        seen_names = {
            str((entry.get("function", {}) or {}).get("name", "")).strip()
            for entry in base_schema
            if isinstance(entry, dict)
        }
        
        # 동적 도구를 OpenAI Function Calling 형식으로 변환
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
        
        # 동적 도구 추가
        for tool in self._dynamic_tools.values():
            # 중복 이름 도구는 스킵 (Native Tool Calling 혼선 방지)
            if tool.name in seen_names:
                logger.debug(
                    "[DynamicToolRegistry] Skipping duplicate tool in schema merge: %s",
                    tool.name,
                )
                continue
            properties: Dict[str, Any] = {}
            required: List[str] = []
            
            for param_name, param_info in tool.parameters.items():
                param_type = param_info.get("type", "string")
                json_type = _convert_type(param_type)
                
                prop_schema: Dict[str, Any] = {
                    "type": json_type,
                    "description": f"Parameter: {param_name}"
                }
                
                if "default" in param_info:
                    prop_schema["default"] = param_info["default"]
                
                properties[param_name] = prop_schema
                
                if param_info.get("required") == "true" or "default" not in param_info:
                    required.append(param_name)
            
            function_schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required if required else [],
                    }
                }
            }
            
            base_schema.append(function_schema)
            seen_names.add(tool.name)
        
        return base_schema

    def get_tool(self, name: str) -> Optional[Tool]:
        """이름으로 도구 조회 (기본 우선, 없으면 동적)."""
        t = self._base.get_tool(name)
        if t is not None:
            return t
        return self._dynamic_tools.get(name)

    async def execute(self, tool_name: str, args: Optional[Dict[str, Any]] = None) -> str:
        """기본 레지스트리 우선 실행, 없으면 동적 도구 실행."""
        if self._base.get_tool(tool_name) is not None:
            return await self._base.execute(tool_name, args)
        tool = self._dynamic_tools.get(tool_name)
        if tool is None:
            available = ", ".join(sorted(self.get_tool_names()))
            return f"[Error] '{tool_name}' 도구를 찾을 수 없습니다. 사용 가능: {available}"

        safe_args = args or {}
        allowed = set(tool.parameters.keys())
        filtered = {k: v for k, v in safe_args.items() if k in allowed}
        try:
            import inspect
            if inspect.iscoroutinefunction(tool.func):
                result = await tool.func(**filtered)
            else:
                result = tool.func(**filtered)
            return str(result)
        except TypeError as e:
            return f"[Error] {tool_name} 인자 오류: {e}"
        except Exception as e:
            logger.exception("[DynamicToolRegistry] execute %s failed", tool_name)
            return f"[Error] {tool_name} 실행 실패: {e}"

    @property
    def _tools(self) -> Dict[str, Tool]:
        """AgentBrain 등에서 len(_registry._tools) 사용 시 통합 뷰 제공."""
        combined = dict(self._base._tools)
        combined.update(self._dynamic_tools)
        return combined


# ═══════════════════════════════════════════════
# Singleton (base registry 래핑)
# ═══════════════════════════════

_dynamic_registry_instance: Optional[DynamicToolRegistry] = None


def get_dynamic_registry(base_registry: Optional[ToolRegistry] = None) -> DynamicToolRegistry:
    """DynamicToolRegistry 싱글톤. base_registry는 최초 호출 시에만 적용."""
    global _dynamic_registry_instance
    if _dynamic_registry_instance is None:
        from mellow_link.core.tool_registry import registry
        _dynamic_registry_instance = DynamicToolRegistry(base_registry or registry)
        base_dir = Path(__file__).resolve().parent.parent
        custom_dir = base_dir / "custom_tools"
        custom_dir.mkdir(parents=True, exist_ok=True)
        _dynamic_registry_instance.set_custom_tools_dir(custom_dir)
        _dynamic_registry_instance.reload_custom_tools()
    return _dynamic_registry_instance
