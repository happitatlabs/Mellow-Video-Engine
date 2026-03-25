"""
Umbrella module: 모든 agent tool 서브모듈 import로 @tool 자동 등록 트리거.

이 모듈을 import하면:
  1. agent_tools_base가 먼저 로드되어 SecurityManager 초기화
  2. 각 도메인 모듈의 @tool 데코레이터가 ToolRegistry에 자동 등록
  3. 마지막에 registry.freeze()로 추가 등록 차단

기존 import 패턴 호환:
  - import mellow_link.core.agent_tools          (side-effect import)
  - from mellow_link.core.agent_tools import X    (re-export via *)
"""

# base (보안 초기화 + 경로 헬퍼) - 반드시 먼저 import
from mellow_link.core.agent_tools_base import *     # noqa: F401,F403
# 도메인별 도구 모듈
from mellow_link.core.agent_tools_filesystem import *  # noqa: F401,F403
from mellow_link.core.agent_tools_docs import *       # noqa: F401,F403  # read_docs_file (structured)
from mellow_link.core.agent_tools_system import *      # noqa: F401,F403
from mellow_link.core.agent_tools_memory import *      # noqa: F401,F403
from mellow_link.core.agent_tools_creative import *    # noqa: F401,F403
from mellow_link.core.agent_tools_agent import *       # noqa: F401,F403
from mellow_link.core.agent_tools_research import *    # noqa: F401,F403

# -----------------------------------------------------------------------------
# Seal ToolRegistry after initial tool loading (V-02)
# -----------------------------------------------------------------------------
# agent_tools 모듈 import가 끝난 이후에는 새로운 도구 등록을 금지한다.
try:
    from mellow_link.core.tool_registry import registry as _global_registry
    _global_registry.freeze()
except Exception:
    pass
