// =========================
// Utility Functions
// =========================

/**
 * 알림 메시지 표시
 * @param {string} message - 표시할 메시지
 * @param {string} type - 알림 타입 ('success', 'error', 'warning', 'info')
 * @param {number} duration - 표시 시간 (ms), 기본값 4000ms
 */
function showNotification(message, type = 'info', duration = 4000) {
    // 기존 알림 컨테이너 찾기 또는 생성
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 10000;
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-width: 400px;
        `;
        document.body.appendChild(container);
    }

    // 알림 요소 생성
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;

    // 타입별 색상 및 아이콘
    const styles = {
        success: { bg: '#10b981', icon: '✓' },
        error: { bg: '#ef4444', icon: '✕' },
        warning: { bg: '#f59e0b', icon: '⚠' },
        info: { bg: '#3b82f6', icon: 'ℹ' }
    };
    const style = styles[type] || styles.info;

    notification.style.cssText = `
        background: ${style.bg};
        color: white;
        padding: 12px 16px;
        border-radius: 8px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
        display: flex;
        align-items: center;
        gap: 10px;
        font-size: 14px;
        animation: slideIn 0.3s ease-out;
        cursor: pointer;
    `;

    notification.innerHTML = `
        <span style="font-size: 18px;">${style.icon}</span>
        <span style="flex: 1;">${escapeHtml(message)}</span>
        <span style="opacity: 0.7; font-size: 18px;">&times;</span>
    `;

    // 클릭 시 닫기
    notification.onclick = () => {
        notification.style.animation = 'slideOut 0.3s ease-in forwards';
        setTimeout(() => notification.remove(), 300);
    };

    container.appendChild(notification);

    // 자동 제거
    setTimeout(() => {
        if (notification.parentNode) {
            notification.style.animation = 'slideOut 0.3s ease-in forwards';
            setTimeout(() => notification.remove(), 300);
        }
    }, duration);

    // 애니메이션 스타일 추가 (한 번만)
    if (!document.getElementById('notification-styles')) {
        const styleSheet = document.createElement('style');
        styleSheet.id = 'notification-styles';
        styleSheet.textContent = `
            @keyframes slideIn {
                from { transform: translateX(100%); opacity: 0; }
                to { transform: translateX(0); opacity: 1; }
            }
            @keyframes slideOut {
                from { transform: translateX(0); opacity: 1; }
                to { transform: translateX(100%); opacity: 0; }
            }
        `;
        document.head.appendChild(styleSheet);
    }

    return notification;
}

/**
 * HTML 이스케이프
 */
function escapeHtml(t) {
    return t ? t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") : "";
}

/**
 * 온도 슬라이더 값 표시 업데이트
 */
function updateTempDisplay() {
    document.getElementById('tempValue').textContent =
        (document.getElementById('tempSlider').value / 100).toFixed(2);
}


// ✅ [P0] 반복 루프 자동 감지 및 강제 중단
function detectRepetitionLoop(newChunk) {
    loopDetectionBuffer += newChunk;

    // 버퍼가 너무 길어지면 앞부분 제거 (최근 500자만 유지)
    if (loopDetectionBuffer.length > 500) {
        loopDetectionBuffer = loopDetectionBuffer.slice(-500);
    }

    // 동일 문구 반복 감지 (4-gram 기반)
    const words = loopDetectionBuffer.split(/\s+/);
    if (words.length < 8) return false;  // 최소 8단어 필요

    // 최근 4단어 추출
    const recentPhrase = words.slice(-4).join(' ');
    if (recentPhrase.length < 10) return false;  // 너무 짧은 문구는 무시

    // 버퍼 내에서 해당 문구 출현 횟수 계산
    const regex = new RegExp(recentPhrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g');
    const matches = loopDetectionBuffer.match(regex);
    const repeatCount = matches ? matches.length : 0;

    if (repeatCount >= loopDetectionThreshold) {
        console.warn(`⛔ [Loop Detection] Phrase repeated ${repeatCount} times: "${recentPhrase}"`);
        return true;
    }

    return false;
}
