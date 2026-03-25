"""
VTuber Relay Service for Mellow-Link

Handles WebSocket communication with the Open-LLM-VTuber avatar service.
Relays conversation messages to the VTuber for speech synthesis and animation.
"""

import asyncio
import json
import logging
import re
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class VTuberConnectionStatus(Enum):
    """Connection status for VTuber service."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class VTuberMessage:
    """Message to send to VTuber avatar."""
    text: str
    emotion: str = "neutral"  # neutral, happy, sad, surprised, angry
    priority: int = 1  # 1=normal, 2=high, 3=urgent
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class VTuberStatus:
    """Status response from VTuber service."""
    connected: bool = False
    speaking: bool = False
    current_emotion: str = "neutral"
    queue_size: int = 0
    last_heartbeat: Optional[datetime] = None


class VTuberRelayService:
    """
    Service for relaying messages to VTuber avatar via WebSocket.

    The VTuber service runs on port 12393 and accepts WebSocket connections
    for text-to-speech and animation control.
    """

    def __init__(
        self,
        ws_url: str = "ws://localhost:12393/client-ws",
        reconnect_interval: float = 5.0,
        heartbeat_interval: float = 30.0
    ):
        """
        Initialize VTuber relay service.

        Args:
            ws_url: WebSocket URL of the VTuber service
            reconnect_interval: Seconds between reconnection attempts
            heartbeat_interval: Seconds between heartbeat pings
        """
        self.ws_url = ws_url
        self.reconnect_interval = reconnect_interval
        self.heartbeat_interval = heartbeat_interval

        self._websocket = None
        self._status = VTuberConnectionStatus.DISCONNECTED
        self._is_running = False
        self._message_queue: asyncio.Queue = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._send_task: Optional[asyncio.Task] = None

        # Callbacks
        self._on_status_change: Optional[Callable] = None
        self._on_message_sent: Optional[Callable] = None
        self._on_error: Optional[Callable] = None

        # Status tracking
        self._last_heartbeat: Optional[datetime] = None
        self._vtuber_status = VTuberStatus()

        # Subtitle auto-clear timer (10초 후 빈 프레임 전송)
        self._clear_timer_task: Optional[asyncio.Task] = None
        self._subtitle_clear_delay: float = 10.0  # seconds

        logger.info(f"[VTuberRelay] Initialized with URL: {ws_url}")

    @property
    def status(self) -> VTuberConnectionStatus:
        """Get current connection status."""
        return self._status

    @property
    def is_connected(self) -> bool:
        """Check if connected to VTuber service."""
        return self._status == VTuberConnectionStatus.CONNECTED

    def get_status(self) -> Dict[str, Any]:
        """Get detailed status information."""
        return {
            "connected": self.is_connected,
            "status": self._status.value,
            "ws_url": self.ws_url,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "vtuber": {
                "speaking": self._vtuber_status.speaking,
                "emotion": self._vtuber_status.current_emotion,
                "queue_size": self._vtuber_status.queue_size
            }
        }

    async def connect(self) -> bool:
        """
        Establish WebSocket connection to VTuber service.

        Returns:
            True if connection successful, False otherwise.
        """
        if self._status == VTuberConnectionStatus.CONNECTED:
            logger.debug("[VTuberRelay] Already connected")
            return True

        self._status = VTuberConnectionStatus.CONNECTING
        logger.info(f"[VTuberRelay] ========================================")
        logger.info(f"[VTuberRelay] Attempting connection to: {self.ws_url}")
        logger.info(f"[VTuberRelay] ========================================")

        try:
            import websockets
            # max_size 10MB: 긴 요약/보고서도 프레임 제한 없이 수신 가능
            # ping_interval=None: 자동 ping 비활성화 (수동 heartbeat로 관리)
            # ping_timeout=None: 수동 heartbeat로 관리
            # close_timeout=5: 종료 시 5초 대기
            self._websocket = await asyncio.wait_for(
                websockets.connect(
                    self.ws_url,
                    max_size=10 * 1024 * 1024,
                    ping_interval=None,  # 자동 ping 비활성화
                    ping_timeout=None,   # 수동 heartbeat로 관리
                    close_timeout=5      # 종료 시 5초 대기
                ),
                timeout=10.0
            )
            self._status = VTuberConnectionStatus.CONNECTED
            self._last_heartbeat = datetime.now()

            logger.info("[VTuberRelay] ✅ CONNECTED SUCCESSFULLY!")
            logger.info(f"[VTuberRelay] WebSocket state: {self._websocket.state if hasattr(self._websocket, 'state') else 'active'}")

            if self._on_status_change:
                await self._on_status_change(self._status)

            return True

        except ImportError:
            logger.error("[VTuberRelay] 흥, websockets 라이브러리가 없군. pip install websockets로 칩을 보충해야 해.")
            self._status = VTuberConnectionStatus.ERROR
            return False
        except asyncio.TimeoutError:
            logger.warning("[VTuberRelay] 후후, 딜러(아바타 서버)가 10초째 응답이 없어. 포트 12393에서 대기 중인지 확인해봐.")
            self._status = VTuberConnectionStatus.DISCONNECTED
            return False
        except Exception as e:
            logger.warning(f"[VTuberRelay] 아바타 서버 연결 실패: {e} (포트 12393에서 Open-LLM-VTuber가 실행 중인지 확인 필요)")
            self._status = VTuberConnectionStatus.ERROR
            return False

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        self._is_running = False

        # Cancel background tasks
        for task in [self._reconnect_task, self._heartbeat_task, self._send_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket with timeout
        if self._websocket:
            try:
                # close_timeout은 connect 시 설정했지만, 명시적으로 대기 시간 제한
                await asyncio.wait_for(
                    self._websocket.close(),
                    timeout=5.0
                )
            except asyncio.TimeoutError:
                logger.warning("[VTuberRelay] WebSocket close timeout, forcing close")
            except Exception as e:
                logger.debug(f"[VTuberRelay] Error closing websocket: {e}")

        self._websocket = None
        self._status = VTuberConnectionStatus.DISCONNECTED
        logger.info("[VTuberRelay] Disconnected")

    async def start(self) -> None:
        """Start the relay service with auto-reconnection."""
        if self._is_running:
            return

        self._is_running = True
        self._message_queue = asyncio.Queue()

        # Start background tasks
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._send_task = asyncio.create_task(self._send_loop())

        logger.info("[VTuberRelay] Service started")

    async def stop(self) -> None:
        """Stop the relay service."""
        await self.disconnect()
        logger.info("[VTuberRelay] Service stopped")

    async def send_message(self, message: VTuberMessage) -> bool:
        """
        Queue a message to be sent to VTuber.

        Args:
            message: VTuberMessage to send

        Returns:
            True if queued successfully
        """
        if not self._message_queue:
            logger.warning("[VTuberRelay] Service not started, cannot queue message")
            return False

        await self._message_queue.put(message)
        logger.debug(f"[VTuberRelay] Message queued: {message.text[:50]}...")
        return True

    async def send_text(
        self,
        text: str,
        emotion: str = "neutral",
        priority: int = 1
    ) -> bool:
        """
        Convenience method to send text to VTuber.

        Args:
            text: Text for VTuber to speak
            emotion: Emotion state (neutral, happy, sad, surprised, angry)
            priority: Message priority (1=normal, 2=high, 3=urgent)

        Returns:
            True if queued successfully
        """
        message = VTuberMessage(text=text, emotion=emotion, priority=priority)
        return await self.send_message(message)

    async def relay_llm_response(
        self,
        response_text: str,
        session_id: Optional[int] = None,
        folder_name: Optional[str] = None
    ) -> bool:
        """
        Relay an LLM response to VTuber for speech.

        This is the main method for integrating with the chat system.
        It automatically detects emotion from text and relays to VTuber.

        Args:
            response_text: The LLM response text
            session_id: Optional session ID for context
            folder_name: Optional folder name (e.g., "Secretary")

        Returns:
            True if relayed successfully
        """
        if not self.is_connected:
            logger.debug("[VTuberRelay] 후후, 아바타와의 회선이 끊겨 있군. 전달은 다음 판으로 미루지.")
            return False

        # Detect emotion from text (simple heuristic)
        emotion = self._detect_emotion(response_text)

        # Higher priority for Secretary folder
        priority = 2 if folder_name and "Secretary" in folder_name else 1

        message = VTuberMessage(
            text=response_text,
            emotion=emotion,
            priority=priority,
            metadata={
                "session_id": session_id,
                "folder_name": folder_name,
                "source": "llm_response"
            }
        )

        return await self.send_message(message)

    def _detect_emotion(self, text: str) -> str:
        """Simple emotion detection from text."""
        text_lower = text.lower()

        # Happy indicators
        happy_words = ["기쁘", "좋아", "축하", "행복", "웃", "ㅎㅎ", "^^", ":)", "happy", "great", "wonderful"]
        if any(word in text_lower for word in happy_words):
            return "happy"

        # Sad indicators
        sad_words = ["슬프", "안타깝", "아쉽", "눈물", "ㅠㅠ", ":(", "sad", "sorry", "unfortunately"]
        if any(word in text_lower for word in sad_words):
            return "sad"

        # Surprised indicators
        surprised_words = ["놀랍", "대박", "와!", "오!", "!!", "wow", "amazing", "incredible"]
        if any(word in text_lower for word in surprised_words):
            return "surprised"

        return "neutral"

    async def _reconnect_loop(self) -> None:
        """Background task for auto-reconnection."""
        while self._is_running:
            if self._status != VTuberConnectionStatus.CONNECTED:
                await self.connect()

            await asyncio.sleep(self.reconnect_interval)

    async def _heartbeat_loop(self) -> None:
        """
        Background task for heartbeat pings.
        
        VRAM 과부하 시 GPU 응답 지연을 고려하여 타임아웃을 설정하고,
        실패 시 즉시 연결 끊김으로 처리하지 않고 재시도합니다.
        """
        while self._is_running:
            if self.is_connected and self._websocket:
                try:
                    # Send ping with timeout (VRAM 과부하 시 응답 지연 대응)
                    await asyncio.wait_for(
                        self._websocket.ping(),
                        timeout=10.0  # 10초 타임아웃 (기본 20초보다 짧게)
                    )
                    self._last_heartbeat = datetime.now()
                except asyncio.TimeoutError:
                    logger.warning(
                        "[VTuberRelay] Heartbeat timeout (VRAM 과부하 가능성). "
                        "연결 상태 확인 중..."
                    )
                    # 타임아웃 시 즉시 연결 끊김 처리하지 않고 상태 확인
                    # 다음 heartbeat에서 재시도
                except Exception as e:
                    logger.warning(f"[VTuberRelay] Heartbeat failed: {e}")
                    self._status = VTuberConnectionStatus.DISCONNECTED

            await asyncio.sleep(self.heartbeat_interval)

    async def _send_loop(self) -> None:
        """Background task for sending queued messages."""
        while self._is_running:
            try:
                # Get message from queue with timeout
                message = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0
                )

                if self.is_connected and self._websocket:
                    await self._send_to_vtuber(message)
                else:
                    # Re-queue if not connected
                    await self._message_queue.put(message)
                    await asyncio.sleep(1.0)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"[VTuberRelay] Send loop error: {e}")
                await asyncio.sleep(2.0)

    def _split_into_sentences(self, text: str, max_length: int = 80) -> List[str]:
        """
        Split text into sentences for sequential TTS delivery.

        한국어 + 영문 구두점을 모두 인식한다:
          - 마침표/물음표/느낌표 (.!? 및 fullwidth ．！？)
          - 줄바꿈
          - 한국어 종결 보조: 다/요/죠/야/지/네/걸/군 + 마침표 패턴은
            기본 구두점 패턴에 이미 포함됨.

        Args:
            text: Text to split
            max_length: Maximum length for a sentence before additional splitting

        Returns:
            List of sentence strings
        """
        if not text or not text.strip():
            return []

        # Normalize line breaks
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Fullwidth → ASCII punctuation 정규화
        text = text.replace('\uff0e', '.').replace('\uff01', '!').replace('\uff1f', '?')

        # 한국어 이모지 태그 구분자 (✅ ⚠️ ❌ 등) 뒤에도 자연스러운 끊김 제공
        text = re.sub(r'([✅⚠️❌])\s*', r'\1 ', text)

        # Split pattern: 문장 끝 구두점 + 공백, 또는 줄바꿈
        sentence_pattern = r'([.!?。！？]+\s*|\n+)'
        parts = re.split(sentence_pattern, text)

        sentences: List[str] = []
        current_sentence = ""

        for part in parts:
            if not part:
                continue

            current_sentence += part

            if re.match(r'^[.!?。！？]+\s*$', part) or '\n' in part:
                sentence = current_sentence.strip()
                if sentence:
                    sentence = re.sub(r'\n+', ' ', sentence)
                    sentence = re.sub(r'\s+', ' ', sentence).strip()

                    if sentence:
                        if len(sentence) > max_length:
                            sentences.extend(self._split_long_sentence(sentence, max_length))
                        else:
                            sentences.append(sentence)
                current_sentence = ""

        # Remaining text
        remaining = current_sentence.strip()
        if remaining:
            remaining = re.sub(r'\n+', ' ', remaining)
            remaining = re.sub(r'\s+', ' ', remaining).strip()
            if remaining:
                if len(remaining) > max_length:
                    sentences.extend(self._split_long_sentence(remaining, max_length))
                else:
                    sentences.append(remaining)

        return [s for s in sentences if s.strip()]
    
    def _split_long_sentence(self, sentence: str, max_length: int = 80) -> List[str]:
        """
        Split a long sentence by commas or spaces.
        
        Args:
            sentence: Long sentence to split
            max_length: Maximum length for each chunk
            
        Returns:
            List of sentence chunks
        """
        if len(sentence) <= max_length:
            return [sentence]
        
        chunks = []
        current_chunk = ""
        
        # First try splitting by comma
        comma_parts = sentence.split(',')
        
        for part in comma_parts:
            part = part.strip()
            if not part:
                continue
            
            # If adding this part would exceed max_length, finalize current chunk
            if current_chunk and len(current_chunk + ', ' + part) > max_length:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = part
            else:
                if current_chunk:
                    current_chunk += ', ' + part
                else:
                    current_chunk = part
        
        # If we still have a chunk that's too long, split by spaces
        if current_chunk and len(current_chunk) > max_length:
            words = current_chunk.split()
            temp_chunk = ""
            for word in words:
                if temp_chunk and len(temp_chunk + ' ' + word) > max_length:
                    if temp_chunk:
                        chunks.append(temp_chunk)
                    temp_chunk = word
                else:
                    if temp_chunk:
                        temp_chunk += ' ' + word
                    else:
                        temp_chunk = word
            if temp_chunk:
                chunks.append(temp_chunk)
        elif current_chunk:
            chunks.append(current_chunk)
        
        return chunks if chunks else [sentence]

    async def _send_to_vtuber(self, message: VTuberMessage) -> bool:
        """
        Send a message to VTuber via WebSocket.
        카드를 한 장씩 돌리듯 문장 단위로 쪼개어 순차 전송한다.
        ENABLE_EDGE_TTS=0 이면 speak 전송 no-op(True 반환, 텍스트만 반환).
        """
        try:
            from mellow_link.config.settings import get_settings
            from mellow_link.core.null_providers import log_airgap_block
            if not get_settings().allow_edge_tts():
                log_airgap_block("VTuberRelayService._send_to_vtuber", "ENABLE_EDGE_TTS", "speak 전송 no-op, 텍스트만 반환")
                return True
        except Exception:
            pass
        try:
            text = message.text.strip()
            if not text:
                return False

            # max_size를 10MB로 올렸으므로 chunk 상한도 여유 있게 설정
            max_chunk_size = 200
            if len(text) <= max_chunk_size:
                sentences = [text]
            else:
                sentences = self._split_into_sentences(text, max_length=max_chunk_size)

            if not sentences:
                logger.warning("[VTuberRelay] 분할 후 전송할 문장 없음")
                return False

            # 최대 10문장까지 허용 (보고서/요약본 대응)
            if len(sentences) > 10:
                logger.warning(
                    f"[VTuberRelay] 문장 {len(sentences)}개 → 10개로 축소"
                )
                sentences = sentences[:10]

            logger.info(
                f"[VTuberRelay] {len(sentences)}장의 카드 전송 시작 "
                f"(전체 길이: {len(message.text)})"
            )

            # 문장별 순차 전송
            for i, sentence in enumerate(sentences):
                if not self.is_connected or not self._websocket:
                    logger.warning(
                        f"[VTuberRelay] 전송 중 연결 끊김 ({i+1}/{len(sentences)})"
                    )
                    return False

                payload = {
                    "type": "speak",
                    "text": sentence.strip(),
                    "emotion": message.emotion,
                    "priority": message.priority,
                    "metadata": message.metadata,
                }

                try:
                    await self._websocket.send(json.dumps(payload))
                    logger.debug(
                        f"[VTuberRelay] [{i+1}/{len(sentences)}] {sentence[:50]}..."
                    )

                    # 문장 사이 호흡 (마지막 문장 뒤에는 대기 불필요)
                    if i < len(sentences) - 1:
                        await asyncio.sleep(1.5)

                except Exception as e:
                    logger.error(
                        f"[VTuberRelay] 문장 {i+1} 전송 실패: {e}"
                    )
                    self._status = VTuberConnectionStatus.DISCONNECTED
                    return False

            if self._on_message_sent:
                await self._on_message_sent(message)

            logger.debug(
                f"[VTuberRelay] {len(sentences)}장 전송 완료"
            )

            # 10초 후 자막 자동 클리어 예약
            self._schedule_subtitle_clear()

            return True

        except Exception as e:
            logger.error(f"[VTuberRelay] 전송 오류: {e}")
            self._status = VTuberConnectionStatus.DISCONNECTED
            return False

    # ── Subtitle Auto-Clear (10초 타이머) ──

    def _schedule_subtitle_clear(self) -> None:
        """
        마지막 전송 시점으로부터 10초 후에 빈 프레임을 보내 자막을 지운다.
        새 메시지가 들어오면 기존 타이머를 취소하고 다시 10초를 세기 시작한다.
        """
        # 기존 타이머 취소 (중복 방지)
        if self._clear_timer_task and not self._clear_timer_task.done():
            self._clear_timer_task.cancel()

        self._clear_timer_task = asyncio.ensure_future(
            self._subtitle_clear_coroutine()
        )

    async def _subtitle_clear_coroutine(self) -> None:
        """10초 대기 후 빈 speak 프레임을 전송하여 자막/InputBox를 클리어."""
        try:
            await asyncio.sleep(self._subtitle_clear_delay)

            if not self.is_connected or not self._websocket:
                return

            clear_payload = {
                "type": "speak",
                "text": "",
                "emotion": "neutral",
                "priority": 0,
                "metadata": {"source": "subtitle_auto_clear"},
            }
            await self._websocket.send(json.dumps(clear_payload))
            logger.debug("[VTuberRelay] 자막 자동 클리어 전송 (10초 경과)")

        except asyncio.CancelledError:
            # 새 메시지가 들어와서 타이머가 취소됨 — 정상 동작
            pass
        except Exception as e:
            logger.debug(f"[VTuberRelay] 자막 클리어 전송 스킵: {e}")

    # Callback setters
    def on_status_change(self, callback: Callable) -> None:
        """Register callback for status changes."""
        self._on_status_change = callback

    def on_message_sent(self, callback: Callable) -> None:
        """Register callback for successful message sends."""
        self._on_message_sent = callback

    def on_error(self, callback: Callable) -> None:
        """Register callback for errors."""
        self._on_error = callback


# =============================================================================
# Factory Function
# =============================================================================

def create_vtuber_relay(
    ws_url: str = "ws://localhost:12393/client-ws",
    reconnect_interval: float = 5.0
) -> VTuberRelayService:
    """
    Factory function to create VTuber relay service.

    Args:
        ws_url: WebSocket URL (default: ws://localhost:12393/client-ws)
        reconnect_interval: Reconnection interval in seconds

    Returns:
        VTuberRelayService instance
    """
    return VTuberRelayService(
        ws_url=ws_url,
        reconnect_interval=reconnect_interval
    )


# =============================================================================
# Global Instance (Singleton pattern)
# =============================================================================

_vtuber_relay: Optional[VTuberRelayService] = None


def get_vtuber_relay() -> Optional[VTuberRelayService]:
    """Get the global VTuber relay instance."""
    return _vtuber_relay


def set_vtuber_relay(relay: VTuberRelayService) -> None:
    """Set the global VTuber relay instance."""
    global _vtuber_relay
    _vtuber_relay = relay
