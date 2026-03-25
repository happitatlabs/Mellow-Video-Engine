"""
Orchestrator 채팅 파이프라인: ChatPipelineProcessor.

Intent 분류, RAG 검색, LLM 생성, 웹 검색, 프롬프트 빌드.
"""
import asyncio
import logging
import re
import time
from typing import Any, Callable, List, Optional, AsyncGenerator, Dict

from .orchestrator_schemas import ChatContext, ChatState, IntentResult
from .output_sanitizer import detect_plan_intent

logger = logging.getLogger(__name__)


class ChatPipelineProcessor:
    """
    채팅 파이프라인 처리: ANALYZING -> RETRIEVING -> GENERATING -> COMPLETED.

    Orchestrator가 서비스, 락, 에이전트 접근을 제공하고,
    이 클래스는 파이프라인 로직만 담당한다.
    """

    def __init__(self, orchestrator_ref: Any):
        """
        Args:
            orchestrator_ref: Orchestrator 인스턴스 (_services, _gpu_lock, agent, _init_agent_if_possible 접근용)
        """
        self._orch = orchestrator_ref

    async def process_chat(
        self,
        context: ChatContext,
        rag_search_fn: Optional[Callable] = None,
        llm_generate_fn: Optional[Callable] = None
    ) -> ChatContext:
        """
        Process a chat request through the full pipeline.

        ANALYZING -> RETRIEVING (optional) -> GENERATING -> GENERATING_RESPONSE -> COMPLETED
        """
        start_time = time.time()

        try:
            context.current_state = ChatState.ANALYZING
            context = await self._analyze_request(context)

            if context.should_use_rag and rag_search_fn:
                context.current_state = ChatState.RETRIEVING
                context = await self._retrieve_documents(context, rag_search_fn)

            context.current_state = ChatState.GENERATING
            if llm_generate_fn:
                context = await self._generate_response(context, llm_generate_fn)

            context.current_state = ChatState.GENERATING_RESPONSE
            logger.info("[Orchestrator] Transitioning to GENERATING_RESPONSE state for final report")

            if not context.final_answer and hasattr(context, 'response'):
                context.final_answer = context.response

            context.current_state = ChatState.COMPLETED

        except Exception as e:
            logger.error(f"[Orchestrator] Chat processing error: {e}", exc_info=True)
            context.current_state = ChatState.ERROR
            context.error_message = str(e)
            context.state_info = "ERROR"

        context.processing_time = time.time() - start_time
        return context

    async def process_chat_stream(
        self,
        context: ChatContext,
        rag_search_fn: Optional[Callable] = None,
        llm_stream_fn: Optional[Callable] = None
    ) -> AsyncGenerator[str, None]:
        """Process chat with streaming response."""
        start_time = time.time()

        try:
            context.current_state = ChatState.ANALYZING
            context = await self._analyze_request(context)

            if context.should_use_rag and rag_search_fn:
                context.current_state = ChatState.RETRIEVING
                context = await self._retrieve_documents(context, rag_search_fn)

            context.current_state = ChatState.GENERATING

            if llm_stream_fn:
                final_prompt = self._build_final_prompt(context)
                async with self._orch._gpu_lock:
                    async for chunk in llm_stream_fn(
                        system_prompt=context.system_prompt,
                        user_prompt=final_prompt,
                        mode=context.selected_mode or context.mode
                    ):
                        context.final_answer += chunk
                        yield chunk

            context.current_state = ChatState.GENERATING_RESPONSE
            logger.info("[Orchestrator] Transitioning to GENERATING_RESPONSE state for final report (streaming)")

            context.current_state = ChatState.COMPLETED

        except Exception as e:
            logger.error(f"[Orchestrator] Stream error: {e}", exc_info=True)
            context.current_state = ChatState.ERROR
            context.error_message = str(e)
            yield f"\n[Error: {str(e)}]"

        context.processing_time = time.time() - start_time

    async def _analyze_request(self, context: ChatContext) -> ChatContext:
        """Analyze the user's question to classify intent."""
        user_query = context.user_query

        image_keywords_ko = [
            "그려", "그림", "이미지", "사진", "만들어", "생성",
            "일러스트", "그래픽", "캐릭터", "배경", "풍경",
            "포스터", "로고", "아이콘", "디자인"
        ]
        image_keywords_en = [
            "draw", "create", "generate", "image", "picture",
            "illustration", "artwork", "painting", "render"
        ]

        query_lower = user_query.lower()

        for kw in image_keywords_ko + image_keywords_en:
            if kw in query_lower:
                logger.info(f"[Orchestrator] Image keyword detected: '{kw}'")
                context.intent_result = IntentResult(
                    intent="image_request",
                    confidence=0.9,
                    metadata={"detected_keyword": kw}
                )
                context.target_service = "image"
                context.state_info = "IMAGE_REQUEST"
                context.refined_prompt = await self._expand_prompt_for_flux(user_query)
                return context

        llm_service = self._orch._services.get("llm")

        if llm_service:
            try:
                analysis_prompt = f"""Analyze the following user input and classify its intent.

User Input: "{user_query}"

Classify into ONE of these categories (respond with keyword only):
1. simple_chat - casual conversation, greetings, small talk
2. image_request - request for picture, image, visual creation
3. document_qa - asking for specific knowledge, documents, data, information

Response (one word only):"""

                raw_intent = await llm_service.generate(
                    prompt=analysis_prompt,
                    max_tokens=10,
                    temperature=0.1
                )
                intent = raw_intent.strip().lower()
                logger.debug(f"[Orchestrator] LLM intent classification: {intent}")

                if "image" in intent:
                    context.intent_result = IntentResult(
                        intent="image_request",
                        confidence=0.95,
                        metadata={"source": "llm_analysis"}
                    )
                    context.target_service = "image"
                    context.state_info = "IMAGE_REQUEST"
                    context.refined_prompt = await self._expand_prompt_for_flux(user_query)

                elif "document" in intent:
                    context.intent_result = IntentResult(
                        intent="document_qa",
                        confidence=0.9,
                        metadata={"source": "llm_analysis"}
                    )
                    context.target_service = "document"
                    context.should_use_rag = True
                    context.state_info = "DOCUMENT_QA"

                else:
                    context.intent_result = IntentResult(
                        intent="simple_chat",
                        confidence=1.0,
                        metadata={"source": "llm_analysis"}
                    )
                    context.target_service = "llm"
                    context.state_info = "SIMPLE_CHAT"

            except Exception as e:
                logger.warning(f"[Orchestrator] LLM intent classification failed: {e}")
                context = self._fallback_intent_classification(context)
        else:
            context = self._fallback_intent_classification(context)

        if context.target_service in ("llm", "document"):
            if context.mode == "auto":
                # Get prompt_category from context if available
                prompt_category = getattr(context, 'prompt_category', None)
                context.selected_mode = self._select_mode_for_query(user_query, prompt_category=prompt_category)
            else:
                context.selected_mode = context.mode

        logger.info(
            f"[Orchestrator] Intent: {context.intent_result.intent if context.intent_result else 'unknown'}, "
            f"Target: {context.target_service}, Mode: {context.selected_mode}"
        )

        return context

    def _fallback_intent_classification(self, context: ChatContext) -> ChatContext:
        """Fallback intent classification using rule-based heuristics."""
        user_query = context.user_query

        doc_patterns = [
            "뭐야", "뭔가요", "알려줘", "설명해", "어떻게",
            "what is", "explain", "how to", "tell me about"
        ]

        query_lower = user_query.lower()
        for pattern in doc_patterns:
            if pattern in query_lower and context.use_rag:
                context.intent_result = IntentResult(
                    intent="document_qa",
                    confidence=0.7,
                    metadata={"source": "fallback_rules"}
                )
                context.target_service = "document"
                context.should_use_rag = True
                context.state_info = "DOCUMENT_QA"
                return context

        context.intent_result = IntentResult(
            intent="simple_chat",
            confidence=0.8,
            metadata={"source": "fallback_rules"}
        )
        context.target_service = "llm"
        context.state_info = "SIMPLE_CHAT"
        return context

    async def _expand_prompt_for_flux(self, korean_prompt: str) -> str:
        """Expand Korean user request into Flux-optimized English prompt."""
        llm_service = self._orch._services.get("llm")

        if not llm_service:
            logger.warning("[Orchestrator] No LLM for prompt expansion, using original")
            return korean_prompt

        try:
            expansion_prompt = f"""You are an expert prompt engineer for the Flux image generation model.

Convert the following Korean image request into an optimized English prompt for Flux.

Korean Request: "{korean_prompt}"

Requirements:
1. Translate to natural, descriptive English
2. Add specific details: composition, lighting, atmosphere, colors
3. Include artistic style (photorealistic, digital art, oil painting, etc.)
4. Add quality modifiers (highly detailed, 8k, professional)
5. Keep the core intent but enhance visual description
6. Output ONLY the final prompt, no explanations

Optimized Flux Prompt:"""

            refined = await llm_service.generate(
                prompt=expansion_prompt,
                max_tokens=200,
                temperature=0.7
            )

            refined_prompt = refined.strip()

            quality_keywords = ["detailed", "8k", "4k", "high quality", "professional"]
            if not any(kw in refined_prompt.lower() for kw in quality_keywords):
                refined_prompt += ", highly detailed, professional quality"

            logger.info(f"[Orchestrator] Prompt expanded: {refined_prompt[:100]}...")
            return refined_prompt

        except Exception as e:
            logger.error(f"[Orchestrator] Prompt expansion failed: {e}")
            return korean_prompt

    def _select_mode_for_query(self, query: str, prompt_category: Optional[str] = None) -> str:
        """
        Select processing mode based on query analysis.

        Priority order:
        1) If prompt_category == "tool": → "thinking"
        2) Else if plan intent detected: → "thinking" (tool-call capable; plan_created emitted deterministically)
        3) Else if deep keyword detected: → "thinking" or "thinking-lite"
        4) Else if short/simple (len < 50 AND no deep/tool signals): → "fast"
        5) Else: → "fast"

        Research mode is selected only if explicitly requested (not handled here).
        """
        # Priority 1: prompt_category == "tool" → thinking
        if prompt_category == "tool":
            logger.debug("[Orchestrator] AUTO_MODE_DECISION | category=tool | selected=thinking")
            return "thinking"

        # Priority 2: Plan intent detection → thinking (robust: tool-call capable; plan_created emitted in agent_brain)
        if detect_plan_intent(query):
            effective_mode = "thinking"
            logger.info("[PLAN_INTENT] detected mode=%s", effective_mode)
            logger.debug("[Orchestrator] AUTO_MODE_DECISION | plan_intent=detected | selected=thinking")
            return effective_mode

        query_lower = query.lower()

        # Priority 3: Deep keyword detection → thinking or thinking-lite
        deep_keywords = [
            '분석', '리포트', '전망', '전략', '계획', '설계',
            '비교', '평가', '검토', '연구', '조사', '탐구',
            'analysis', 'report', 'strategy', 'plan', 'research',
            'compare', 'evaluate', 'review', 'investigate'
        ]

        has_deep_keyword = False
        detected_keyword = None
        for keyword in deep_keywords:
            if keyword in query_lower:
                has_deep_keyword = True
                detected_keyword = keyword
                break
        
        if has_deep_keyword:
            # thinking-lite 조건: deep keyword 있음 AND tool 키워드 없음 AND "report" 명시 없음
            tool_keywords = [
                '파일', '폴더', '경로', '읽어', '써', '저장', '삭제', '업로드', '문서', '인덱싱', '검색', 'rag',
                'file', 'folder', 'path', 'read', 'write', 'save', 'delete', 'upload', 'document', 'index', 'search', 'rag'
            ]
            has_tool_keyword = any(kw in query_lower for kw in tool_keywords)
            has_explicit_report = 'report' in query_lower or '리포트' in query_lower
            
            if not has_tool_keyword and not has_explicit_report:
                logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | keyword={detected_keyword} | selected=thinking-lite")
                return "thinking-lite"
            else:
                logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | keyword={detected_keyword} | selected=thinking")
                return "thinking"

        # Priority 3: Short/simple message → fast (only if no deep/tool signals)
        if len(query) < 50:
            # Check for tool-required keywords (if present, should go to thinking)
            tool_required_keywords = [
                '파일', '폴더', '경로', '읽어', '써', '저장', '삭제', '업로드', '문서', '인덱싱', '검색', 'rag',
                'file', 'folder', 'path', 'read', 'write', 'save', 'delete', 'upload', 'document', 'index', 'search', 'rag'
            ]
            has_tool_keyword = any(keyword in query_lower for keyword in tool_required_keywords)
            if has_tool_keyword:
                logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | tool_keyword_detected | selected=thinking")
                return "thinking"
            
            # Check for lightweight system tool keywords (should use thinking mode to call tools)
            lightweight_tool_keywords = [
                '현재 작업 디렉토리', '작업 디렉토리', '현재 디렉토리', 'cwd',
                '현재 시간', '몇 시', '시간 알려', 'time',
                '시스템 정보', '메모리', '디스크', '시스템 상태', 'system info', 'memory', 'disk',
                '프로세스 목록', '프로세스', 'process', 'processes'
            ]
            has_lightweight_keyword = any(keyword in query_lower for keyword in lightweight_tool_keywords)
            if has_lightweight_keyword:
                logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | lightweight_tool_keyword_detected | selected=thinking")
                return "thinking"
            
            # Simple exclamation detection (high consonant ratio)
            hangul_consonants = re.findall(r'[ㅋㅎㄷㄱㅅㅈㅂㄴㅁㅇㄹ]+', query)
            consonant_ratio = sum(len(c) for c in hangul_consonants) / max(len(query), 1)
            if consonant_ratio > 0.5:
                logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | simple_exclamation | selected=fast")
                return "fast"
            
            logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | short_message | selected=fast")
            return "fast"

        # Priority 4: Default → fast
        logger.debug(f"[Orchestrator] AUTO_MODE_DECISION | category={prompt_category} | default | selected=fast")
        return "fast"

    async def _retrieve_documents(
        self,
        context: ChatContext,
        rag_search_fn: Callable
    ) -> ChatContext:
        """Retrieve relevant documents using RAG."""
        if not context.rag_collection_name:
            logger.warning("[Orchestrator] No RAG collection specified")
            context.should_use_rag = False
            return context

        try:
            search_query = context.user_query
            if len(context.user_query) < 20 and context.session_history:
                last_user_msg = next(
                    (msg['content'] for msg in reversed(context.session_history)
                     if msg.get('role') == 'user'),
                    ''
                )
                if last_user_msg:
                    search_query = f"{last_user_msg} {context.user_query}"
                    logger.debug(f"[Orchestrator] Context-aware search: {search_query[:100]}...")

            results = rag_search_fn(
                query=search_query,
                collection_name=context.rag_collection_name,
                k=5
            )

            if results and len(results) > 0:
                context_parts = []
                for i, hit in enumerate(results[:3], 1):
                    text = hit.get("text", "")
                    metadata = hit.get("metadata", {})
                    source = metadata.get("source_file", metadata.get("source", "Unknown"))
                    context_parts.append(f"[Document {i}] {source}\n{text}")

                    context.rag_sources.append({
                        "text": text[:200],
                        "source": source,
                        "score": hit.get("score", 0.0)
                    })

                context.rag_context = "\n\n".join(context_parts)
                context.rag_used = True
                context.state_info = "RAG_USED"
                logger.info(f"[Orchestrator] Retrieved {len(results)} documents")
            else:
                context.should_use_rag = False
                context.state_info = "RAG_NO_RESULTS"
                logger.info("[Orchestrator] No RAG results found")

        except Exception as e:
            logger.error(f"[Orchestrator] RAG retrieval error: {e}")
            context.should_use_rag = False
            context.state_info = "RAG_ERROR"

        return context

    async def _generate_response(
        self,
        context: ChatContext,
        llm_generate_fn: Callable
    ) -> ChatContext:
        """Generate LLM response. Research 모드일 때 웹 검색 수행."""
        final_prompt = self._build_final_prompt(context)

        web_search_results = ""
        if (context.selected_mode or context.mode) == "research":
            web_search_results = await self._perform_web_search(context.user_query)
            if web_search_results:
                logger.info(f"[Orchestrator] Web search completed for research mode: {len(web_search_results)} chars")
                final_prompt = (
                    f"=== 웹 검색 결과 (최신 정보) ===\n{web_search_results}\n\n"
                    f"{final_prompt}"
                )

        try:
            async with self._orch._gpu_lock:
                self._orch._init_agent_if_possible()
                if self._orch.agent is not None:
                    user_input = final_prompt
                    system_prompt = context.system_prompt or ""

                    if (context.selected_mode or context.mode) == "research":
                        if not system_prompt:
                            system_prompt = ""
                        system_prompt += (
                            "\n\n[중요] Research 모드입니다. "
                            "최신 정보나 사실 확인이 필요하면 web_search 도구를 사용하세요. "
                            "웹 검색 결과가 위에 제공되었지만, 추가 검색이 필요하면 언제든지 web_search 도구를 호출할 수 있습니다."
                        )

                    if system_prompt:
                        user_input = (
                            "=== System Prompt ===\n"
                            f"{system_prompt}\n\n"
                            f"{user_input}"
                        )

                    context_messages = context.session_history or []
                    agent_result = await self._orch.agent.run(user_input, context_messages)
                    context.final_answer = agent_result.answer
                else:
                    context.final_answer = await llm_generate_fn(
                        system_prompt=context.system_prompt,
                        user_prompt=final_prompt,
                        mode=context.selected_mode or context.mode
                    )
            logger.info("[Orchestrator] LLM generation completed")
        except Exception as e:
            logger.error(f"[Orchestrator] LLM generation error: {e}")
            raise

        return context

    async def _perform_web_search(self, query: str) -> str:
        """Research 모드에서 웹 검색 수행."""
        try:
            from mellow_link.core.dynamic_registry import get_dynamic_registry

            registry = get_dynamic_registry()

            if "web_search" in registry.get_tool_names():
                result = await asyncio.to_thread(
                    registry.execute,
                    "web_search",
                    {"query": query, "max_results": 5}
                )
                return result
            else:
                logger.warning("[Orchestrator] web_search 도구를 찾을 수 없습니다.")
                return ""
        except Exception as e:
            logger.error(f"[Orchestrator] Web search failed: {e}")
            return ""

    def _build_final_prompt(self, context: ChatContext) -> str:
        """Build the final user prompt with all context."""
        parts = []

        if context.user_memories:
            memory_text = "\n".join([f"- {mem}" for mem in context.user_memories[:3]])
            parts.append(f"=== User Preferences ===\n{memory_text}")

        if context.rag_used and context.rag_context:
            parts.append(f"=== Reference Documents ===\n{context.rag_context}")

        if context.session_history:
            history_parts = []
            for msg in context.session_history[-5:]:
                role = msg.get("role", "")
                content = msg.get("content", "")[:200]
                if role and content:
                    history_parts.append(f"{role.upper()}: {content}")
            if history_parts:
                parts.append("=== Recent Conversation ===\n" + "\n".join(history_parts))

        parts.append(f"=== Current Question ===\n{context.user_query}")

        return "\n\n".join(parts)
