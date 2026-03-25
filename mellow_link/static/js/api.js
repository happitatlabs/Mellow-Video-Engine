// =========================
// API Module — State API 사용
// =========================

// =========================
// AbortController Lifecycle
// =========================

/**
 * 새 AbortController 생성
 */
function createAbort() {
    var ctrl = new AbortController();
    State.setAbortController(ctrl);
    return ctrl;
}

/**
 * 현재 활성 요청 중단
 */
function abortActive() {
    var ctrl = State.getAbortController();
    if (ctrl) {
        ctrl.abort();
        State.setAbortController(null);
    }
}

/**
 * 중단 후 상태 초기화
 */
function stopGeneration() {
    abortActive();
    State.setIsGenerating(false);
    updateSendButtonState(false);
    document.getElementById('statusText').textContent = 'Stopped';
}

// =========================
// API Fetch Wrappers
// =========================

/**
 * Authorization 헤더 자동 추가 fetch
 */
async function apiFetch(path, options = {}) {
    const headers = { ...options.headers };
    var token = State.getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(`${State.getApiBase()}${path}`, { ...options, headers });
}

/**
 * SSE 스트리밍 요청
 * @param {string} path - API 경로
 * @param {object} payload - 요청 body
 * @param {function} onDataLine - 데이터 라인 콜백 (line) => void
 * @param {AbortSignal} signal - abort signal
 */
async function apiStreamAsk(path, payload, onDataLine, signal) {
    const headers = { 'Content-Type': 'application/json' };
    var token = State.getAuthToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const response = await fetch(`${State.getApiBase()}${path}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal
    });

    if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            if (line.trim()) {
                onDataLine(line);
            }
        }
    }
}

// =========================
// VRAM Monitoring API
// =========================

/**
 * VRAM 상태 조회
 */
async function fetchVRAMStatus() {
    try {
        const res = await apiFetch('/vram-status');
        if (res.ok) {
            const data = await res.json();
            var vs = State.getVRAMStatus();
            vs.used = data.used || 0;
            vs.total = data.total || 0;
            vs.percent = data.percent || 0;
            vs.lastUpdate = new Date();
            updateVRAMWidget();
            return data;
        }
    } catch (e) {
        console.warn('[VRAM] Status fetch failed:', e);
    }
    return null;
}

/**
 * VRAM 폴링 시작 (5초 간격)
 */
function startVRAMPolling() {
    if (State.getVRAMPollInterval()) clearInterval(State.getVRAMPollInterval());
    fetchVRAMStatus();
    State.setVRAMPollInterval(setInterval(function () { fetchVRAMStatus(); }, 5000));

    console.log('[VRAM] Polling started (5s interval)');
}

/**
 * VRAM 폴링 중지
 */
function stopVRAMPolling() {
    var iv = State.getVRAMPollInterval();
    if (iv) {
        clearInterval(iv);
        State.setVRAMPollInterval(null);
        console.log('[VRAM] Polling stopped');
    }
}
