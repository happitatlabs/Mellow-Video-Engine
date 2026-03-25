# System Configuration Reference

Generated: 2026-01-22
Updated: 2026-02-15

Purpose: Agent reference for locating models, configs, and service endpoints without asking.

---

## Data Flow: User Input → Voice Output

### Path A: Mellow-Link Web UI (Primary)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  USER INPUT (Web UI: localhost:8000/ui)                                     │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ POST /chat/ask
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  MELLOW-LINK FastAPI (Port 8000, 기본)                                      │
│  File: mellow_link/routers/chat.py (@router.post("/chat/ask"))              │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ llm_service.generate_stream()
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  OLLAMA LLM (Port 11434)                                                    │
│  Model: exaone-local                                                        │
│  URL: http://localhost:11434                                                │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ Response text (streaming)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  VTuberRelayService                                                         │
│  File: mellow_link/services/vtuber_relay.py:251 (relay_llm_response)        │
│  - Detects emotion from text                                                │
│  - Splits long text into sentences (max 150 chars)                          │
│  - Queues messages for sending                                              │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ WebSocket: {"type": "speak", "text": "..."}
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  OPEN-LLM-VTUBER WebSocket Handler (Port 12393)                             │
│  URL: ws://localhost:12393/client-ws                                        │
│  File: Open-LLM-VTuber/src/open_llm_vtuber/websocket_handler.py:92          │
│  Handler: _handle_conversation_trigger (msg_type="speak")                   │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ Bypass LLM (direct TTS)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  process_speak_direct()                                                     │
│  File: Open-LLM-VTuber/src/open_llm_vtuber/conversations/conversation_handler.py:36
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  TTS Manager                                                                │
│  File: Open-LLM-VTuber/src/open_llm_vtuber/conversations/tts_manager.py     │
│  - Queues TTS tasks                                                         │
│  - Maintains ordered delivery                                               │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ tts_engine.generate_audio(text)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  EDGE TTS (Microsoft)                                                       │
│  File: Open-LLM-VTuber/src/open_llm_vtuber/tts/edge_tts.py                  │
│  Voice: ko-KR-InJoonNeural                                                  │
│  rate: '-10% \sim -15%'                                                     │
│  pitch: '-2Hz \sim +3Hz'                                                    │
│  volume: '+10%' # volume                                                    │
│  Output: MP3 file in cache/                                                 │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ Audio payload (base64 encoded)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  WebSocket → Frontend → Speakers                                            │
│  + Live2D model lip sync & expressions                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Path B: VTuber Direct Voice Input

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  USER VOICE INPUT (Microphone via VTuber Frontend)                          │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ WebSocket: mic-audio-data / mic-audio-end
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  OPEN-LLM-VTUBER WebSocket Handler (Port 12393)                             │
│  File: websocket_handler.py:88 (_handle_audio_data)                         │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  ASR: Sherpa-ONNX SenseVoice                                                │
│  Model: ./models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/        │
│  Provider: CPU                                                              │
│  Output: Transcribed text                                                   │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  process_single_conversation()                                              │
│  File: conversations/single_conversation.py:25                              │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ context.agent_engine.chat(batch_input)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  LLM Agent (Ollama)                                                         │
│  Model: qwen2.5:7b                                                          │
│  URL: http://localhost:11434/v1                                             │
└─────────────────────────────┬───────────────────────────────────────────────┘
                              │ Response text (streaming sentences)
                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  TTS Manager → EdgeTTS → Audio → WebSocket → Speakers                       │
│  (Same as Path A from TTS Manager onwards)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Files in Data Flow

| Step | File | Function/Endpoint |
|------|------|-------------------|
| 1. Web UI Input | `mellow_link/routers/chat.py` | `@router.post("/chat/ask")` |
| 2. LLM Call | `mellow_link/services/llm_service.py` | `generate_stream()` |
| 3. Relay to Avatar | `mellow_link/services/vtuber_relay.py` | `relay_llm_response()` :251 |
| 4. WebSocket Receive | `websocket_handler.py` | `_handle_conversation_trigger()` |
| 5. Direct TTS | `conversation_handler.py` | `process_speak_direct()` :36 |
| 6. TTS Generation | `tts/edge_tts.py` | `generate_audio()` :54 |
| 7. Audio Delivery | `tts_manager.py` | `speak()` → `_process_payload_queue()` |

---

## Network Addresses and Routes

### IPv4 Addresses

| Interface | IP Address | Subnet | DHCP | Gateway |
|-----------|------------|--------|------|---------|
| **Tailscale** | 100.97.4.70 | /32 (255.255.255.255) | No | - |
| **Ethernet** (Realtek Gaming 2.5GbE) | 172.30.1.89 | /24 (255.255.255.0) | Yes | 172.30.1.254 |
| **Loopback** | 127.0.0.1 | /8 (255.0.0.0) | No | - |

### IPv6 Addresses

| Interface | Address |
|-----------|---------|
| **Loopback** | ::1 |
| **Tailscale** | fd7a:115c:a1e0::6601:446 |
| **Tailscale** | fe80::340e:d267:6cef:8cf1%6 |
| **Ethernet** | fe80::a173:e0f5:f779:e9e8%11 |

### IPv4 Routing Table

| Network | Netmask | Gateway | Interface | Metric |
|---------|---------|---------|-----------|--------|
| 0.0.0.0 | 0.0.0.0 | 172.30.1.254 | 172.30.1.89 | 25 |
| 100.97.4.70 | 255.255.255.255 | On-link | 100.97.4.70 | 261 |
| 100.97.148.128 | 255.255.255.255 | On-link | 100.97.4.70 | 5 |
| 100.100.100.100 | 255.255.255.255 | On-link | 100.97.4.70 | 5 |
| 100.100.215.64 | 255.255.255.255 | On-link | 100.97.4.70 | 5 |
| 100.124.230.38 | 255.255.255.255 | On-link | 100.97.4.70 | 5 |
| 127.0.0.0 | 255.0.0.0 | On-link | 127.0.0.1 | 331 |
| 172.30.1.0 | 255.255.255.0 | On-link | 172.30.1.89 | 281 |

---

## Service Ports

| Port | Service | Description |
|------|---------|-------------|
| **8000** | Mellow-Link API | FastAPI server (main backend) |
| **12393** | Open-LLM-VTuber | Avatar WebSocket service |
| **11434** | Ollama | LLM inference server |
| **8188** | ComfyUI | Image generation (if running) |

---

## Key File Paths

### Configuration Files

| File | Path |
|------|------|
| **Mellow-Link .env** | `D:\AI_Project\mellow_link\.env` |
| **Mellow-Link settings.py** | `D:\AI_Project\mellow_link\config\settings.py` |
| **Open-LLM-VTuber conf.yaml** | `D:\AI_Project\Open-LLM-VTuber\conf.yaml` |

### Live2D Models

| Location | `D:\AI_Project\Open-LLM-VTuber\live2d\` |
|----------|------------------------------------------|

**Available Models:**
- `haruto` (currently active)
- `koharu`
- `mao_pro`
- `shizuku`

**Current Model Config (from conf.yaml):**
```yaml
live2d_model_name: 'haruto'
character_name: 'haruto'
```

### Data Directories

| Purpose | Path |
|---------|------|
| **AI Models** | `D:\AI_Hub\Models` |
| **Data** | `D:\AI_Hub\Data` |
| **Outputs** | `D:\AI_Hub\Data\outputs` |

---

## APIs in Use

### LLM Backend: Ollama

| Setting | Value |
|---------|-------|
| **Provider** | Ollama |
| **Base URL** | `http://localhost:11434` |
| **Model (Mellow-Link)** | `exaone-local` |
| **Model (VTuber)** | `qwen2.5:7b` |
| **Keep Alive** | `-1` (forever) |

### TTS Backend: EdgeTTS

| Setting | Value |
|---------|-------|
| **Provider** | Edge TTS |
| **Voice** | `ko-KR-InJoonNeural` | 
| **Rate** | `-10% \sim -15%` |
| **Pitch** | `-2Hz \sim +3Hz` |
| **Volume** | `+10%` |

### ASR Backend: Sherpa-ONNX (SenseVoice)

| Setting | Value |
|---------|-------|
| **Model Type** | `sense_voice` |
| **Model Path** | `./models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17/model.int8.onnx` |
| **Provider** | CPU |

---

## Service URLs Quick Reference

```
Mellow-Link API:     http://localhost:8000
Mellow-Link UI:      http://localhost:8000/ui
VTuber WebSocket:    ws://localhost:12393/client-ws
Ollama API:          http://localhost:11434
ComfyUI:             http://localhost:8188 (if running)
```

---

## Network Summary

- **Primary network**: Ethernet on 172.30.1.89 with default gateway 172.30.1.254
- **VPN overlay**: Tailscale on 100.97.4.70 (CGNAT range for mesh networking)
- Default route goes through the Ethernet interface to gateway 172.30.1.254

---

## Voice Pipeline Summary

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   User       │    │  Mellow-Link │    │   Ollama     │    │  VTuber      │    │   EdgeTTS    │
│   Input      │───▶│   :8000      │───▶│   :11434     │───▶│   :12393     │───▶│   (Cloud)    │
│   (Text)     │    │   FastAPI    │    │   LLM        │    │   WebSocket  │    │   TTS        │
└──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘    └──────────────┘
                                                                   │
                                                                   ▼
                                                            ┌──────────────┐
                                                            │   Audio      │
                                                            │   Output     │
                                                            │   (Voice)    │
                                                            └──────────────┘
```

### Conditional Persona Logic

```
┌────────────────────┐                    ┌─────────────────────────────┐
│  Web UI :8000      │───────────────────▶│  Default System Prompt      │
└────────────────────┘                    └─────────────────────────────┘

┌────────────────────┐                    ┌─────────────────────────────┐
│  VTuber WS :12393  │───────────────────▶│  aventurine_persona_v1.txt  │
│  (speak type)      │                    │  (Character roleplay mode)  │
└────────────────────┘                    └─────────────────────────────┘
```

| Trigger | Action |
|---------|--------|
| Connection from port 12393 | Override with `aventurine_persona_v1.txt` |
| Message type = `"speak"` | Aventurine character mode activated |
| Scope | VTuber interface only (Web UI unaffected) |

**Persona File:** `D:\AI_Project\mellow_link\prompts\aventurine_persona_v1.txt`

---

### Quick Reference: "Where does X happen?"

| Question | Answer |
|----------|--------|
| Where is the model file? | `D:\AI_Project\Open-LLM-VTuber\live2d\haruto\` |
| Where is the config? | `D:\AI_Project\Open-LLM-VTuber\conf.yaml` |
| Where does text become speech? | `Open-LLM-VTuber/src/open_llm_vtuber/tts/edge_tts.py` |
| Where does speech become text? | `Open-LLM-VTuber/src/open_llm_vtuber/asr/` (SenseVoice) |
| Where does LLM generate response? | Ollama @ `localhost:11434` |
| Where does Mellow-Link relay to avatar? | `mellow_link/services/vtuber_relay.py` |
| Where are audio files cached? | `D:\AI_Project\Open-LLM-VTuber\cache\` |
| Which voice is used? | `ko-KR-InJoonNeural` (Korean male) |
| Which LLM model? | `exaone-local` (Mellow-Link), `qwen2.5:7b` (VTuber direct) |
