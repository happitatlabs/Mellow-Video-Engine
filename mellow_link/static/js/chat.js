// =========================
// Legacy Chat Compatibility Shell
// - deprecated legacy UI
// - 신규 사용자 진입 금지
// - 호환/과거 링크 보호용
// - 제거 후보
// =========================

(function () {
    const LEGACY_NOTICE = {
        title: 'Legacy UI',
        body: 'Deprecated 상태입니다. 신규 채팅 플로우는 비활성화되어 있으며 Runtime Console 사용을 권장합니다.',
        cta: '/runtime-console',
    };

    function el(id) {
        return document.getElementById(id);
    }

    function setStatus(message, tone) {
        const status = el('statusText');
        if (!status) return;
        status.textContent = message;
        status.classList.remove('text-yellow-400', 'text-red-400', 'text-sky-300');
        if (tone) status.classList.add(tone);
    }

    function ensureBanner() {
        if (document.getElementById('legacyRuntimeBanner')) return;
        const main = document.querySelector('main.flex-1') || document.querySelector('main');
        if (!main) return;
        const banner = document.createElement('div');
        banner.id = 'legacyRuntimeBanner';
        banner.className = 'mx-6 mt-4 rounded-2xl border border-amber-400/30 bg-amber-500/10 px-5 py-4 text-sm text-amber-100';
        banner.innerHTML = '<div class="flex flex-wrap items-center justify-between gap-3">' +
            '<div>' +
            '<div class="text-xs uppercase tracking-[0.2em] text-amber-300">Legacy UI</div>' +
            '<div class="mt-1 font-medium">Deprecated</div>' +
            '<div class="mt-1 text-amber-100/80">Runtime Console 사용 권장</div>' +
            '</div>' +
            '<a href="' + LEGACY_NOTICE.cta + '" class="px-4 py-2 rounded-full border border-sky-300/30 bg-sky-400/10 text-sky-200 hover:bg-sky-400/20 transition">Open Runtime Console</a>' +
            '</div>';
        main.insertBefore(banner, main.firstChild);
    }

    function disableLegacyFlow() {
        const input = el('messageInput');
        if (input) {
            input.disabled = true;
            input.placeholder = 'Legacy UI is deprecated. Use Runtime Console.';
            input.classList.add('opacity-60', 'cursor-not-allowed');
        }
        const sendBtn = el('sendBtn');
        if (sendBtn) {
            sendBtn.disabled = true;
            sendBtn.classList.add('opacity-60', 'cursor-not-allowed');
            sendBtn.title = 'Legacy chat disabled';
        }
        const fileInput = el('fileInput');
        if (fileInput) fileInput.disabled = true;
        setStatus('Legacy UI deprecated. Runtime Console을 사용하세요.', 'text-yellow-400');
    }

    function appendNoticeOnce() {
        const container = el('chatMessages');
        if (!container || document.getElementById('legacyNoticeMessage')) return;
        const wrap = document.createElement('div');
        wrap.id = 'legacyNoticeMessage';
        wrap.className = 'flex justify-start mb-4';
        wrap.innerHTML = '<div class="max-w-[85%] rounded-xl px-4 py-3 border border-amber-400/20 bg-amber-500/10 text-amber-100">' +
            '<div class="text-xs uppercase tracking-[0.2em] text-amber-300">Legacy UI</div>' +
            '<div class="mt-2 whitespace-pre-wrap">' + LEGACY_NOTICE.body + '</div>' +
            '<a href="' + LEGACY_NOTICE.cta + '" class="inline-block mt-3 text-sky-200 hover:text-sky-100 underline">Runtime Console로 이동</a>' +
            '</div>';
        container.innerHTML = '';
        container.appendChild(wrap);
    }

    function showLegacyNotice(action) {
        const suffix = action ? ' (' + action + ')' : '';
        setStatus('Legacy chat disabled' + suffix + '. Runtime Console을 사용하세요.', 'text-yellow-400');
        appendNoticeOnce();
        return false;
    }

    window.sendMessage = async function () {
        return showLegacyNotice('send');
    };

    window.handleChatAction = function () {
        return showLegacyNotice('handleChatAction');
    };

    window.loadSession = async function (id, folderId) {
        if (window.State && typeof State.setCurrentSessionId === 'function' && id != null) {
            State.setCurrentSessionId(id);
        }
        if (window.State && typeof State.setCurrentFolderId === 'function') {
            State.setCurrentFolderId(folderId == null ? null : folderId);
        }
        showLegacyNotice('loadSession');
    };

    window.startEditMessage = async function () {
        return showLegacyNotice('edit');
    };

    window.regenerateResponse = async function () {
        return showLegacyNotice('regenerate');
    };

    window.continueResponse = async function () {
        return showLegacyNotice('continue');
    };

    window.submitFeedback = async function () {
        return showLegacyNotice('feedback');
    };

    document.addEventListener('DOMContentLoaded', function () {
        ensureBanner();
        disableLegacyFlow();
        appendNoticeOnce();
    });
})();
