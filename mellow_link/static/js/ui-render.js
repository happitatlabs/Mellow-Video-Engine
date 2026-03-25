// =========================
// UI Rendering Module
// =========================

/**
 * 전송 버튼 상태 업데이트 (응답 올 때까지 로딩/중지 UI)
 * - generating 시 버튼은 '중지'로 전환되며 클릭 가능(중지 동작)
 * - 중복 전송 방지는 sendMessage 내 isGenerating 체크 + 서버 409 락으로 처리
 */
function updateSendButtonState(generating) {
    const btn = document.getElementById('sendBtn');
    const icon = document.getElementById('sendIcon');
    if (!btn) return;
    if (generating) {
        btn.setAttribute('aria-busy', 'true');
        btn.classList.replace('bg-purple-600', 'bg-red-600');
        btn.classList.replace('hover:bg-purple-700', 'hover:bg-red-700');
        icon.classList.replace('fa-paper-plane', 'fa-stop');
    } else {
        btn.removeAttribute('aria-busy');
        btn.classList.replace('bg-red-600', 'bg-purple-600');
        btn.classList.replace('hover:bg-red-700', 'hover:bg-purple-700');
        icon.classList.replace('fa-stop', 'fa-paper-plane');
    }
}

// ✅ [Edit] 옵션 B: 수정 모드 경고 바 표시/숨김
function showEditWarningBar({ text, onCancel }) {
    const bar = document.getElementById('editWarningBar');
    if (!bar) return;
  
    bar.innerHTML = `
      <div class="edit-warning-text">${escapeHtml(text)}</div>
      <button id="editCancelBtn" class="edit-warning-cancel">수정 취소</button>
    `;
    bar.style.display = 'flex';
  
    const btn = document.getElementById('editCancelBtn');
    btn.onclick = onCancel;
  }
  
  function hideEditWarningBar() {
    const bar = document.getElementById('editWarningBar');
    if (!bar) return;
    bar.style.display = 'none';
    bar.innerHTML = '';
  }

// [중요] 메시지 렌더링 (시간 표시 & 재생성 & 스마트 카피 복구 & 사용자 메시지 수정)
// taskBlockState: optional { runId, title, todos, progress, status, summary } for in-chat TaskBlock (User View)
// planCard: optional { run_id, todos, user_input } for restored plan card + 진행하기 (세션 로드 시)
// evolutionPayload: optional string (JSON) — when content is patch_report, full evolution_report for collapsed "상세"
function addMessageToUI(role, content, ragUsed=false, messageId=null, feedbackPositive=null, autoSelected=false, selectedMode=null, timeTaken=null, originMid=null, taskBlockState=null, planCard=null, evolutionPayload=null) {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = `flex ${role==='user'?'justify-end':'justify-start'} mb-4`;

    let patchReportData = null;
    let taskBlockReportData = null;
    let evolutionReportData = null;
    if (role === 'assistant' && typeof content === 'string' && content.trim()) {
        try {
            const raw = content.trim();
            if ((raw.startsWith('{') && raw.endsWith('}')) || (raw.startsWith('[') && raw.endsWith(']'))) {
                const parsed = JSON.parse(raw);
                if (parsed && parsed.type === 'patch_report') patchReportData = parsed;
                else if (parsed && parsed.type === 'task_block') taskBlockReportData = parsed;
                else if (parsed && parsed.type === 'evolution_report') evolutionReportData = parsed;
            }
        } catch (_) {}
    }

    let processed = content;
    if (patchReportData) {
        const summary = (patchReportData.summary || 'Patch report').toString();
        const hasEvolution = evolutionPayload && (typeof evolutionPayload === 'string' ? evolutionPayload.trim() : evolutionPayload);
        const evolutionSection = hasEvolution
            ? '<details class="evolution-payload-details mt-3 border border-gray-700 rounded-lg overflow-hidden"><summary class="cursor-pointer px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 text-xs text-slate-400">🏛️ 상세 (Evolution 원문)</summary><div class="evolution-payload-container px-3 py-2"></div></details>'
            : '';
        processed = '<div class="actual-answer"><div class="patch-report-label text-xs text-slate-500 mb-2">📋 ' + escapeHtml(summary) + '</div><div class="patch-report-container"></div>' + evolutionSection + '</div>';
    } else if (taskBlockReportData) {
        const title = (taskBlockReportData.title || 'Task').toString();
        processed = '<div class="actual-answer"><div class="autonomous-task-block-label text-xs text-slate-500 mb-2">⚙️ ' + escapeHtml(title) + '</div><div class="autonomous-task-block-container"></div></div>';
    } else if (evolutionReportData) {
        const req = (evolutionReportData.proposal && evolutionReportData.proposal.user_request) ? evolutionReportData.proposal.user_request : '';
        const label = (req.toString().slice(0, 60) || 'Evolution report') + (req.length > 60 ? '...' : '');
        processed = '<div class="actual-answer"><div class="evolution-report-label text-xs text-slate-500 mb-2">🏛️ ' + escapeHtml(label) + '</div><div class="evolution-report-container"></div></div>';
    } else if(role==='assistant' && content.includes('<think>')) {
        const parts = content.split('</think>');
        // ✅ [P0] DOM 구조 안정성: thought-content 클래스 추가 (switchVersion에서 사용)
        processed = `<details class="mb-3 opacity-60 text-xs border-b border-gray-700 pb-2 w-full"><summary class="cursor-pointer hover:text-purple-400">🤔 생각 과정</summary><div class="thought-content p-3 mt-2 bg-black bg-opacity-40 rounded italic whitespace-pre-wrap">${escapeHtml(parts[0].replace('<think>','').trim())}</div></details><div class="actual-answer whitespace-pre-wrap">${escapeHtml(parts[1]?parts[1].trim():"")}</div>`;
    } else {
        // ✅ [P0] DOM 구조 안정성: assistant 메시지에 actual-answer 클래스 보장
        processed = role === 'assistant'
            ? `<div class="actual-answer whitespace-pre-wrap">${escapeHtml(content)}</div>`
            : `<div class="whitespace-pre-wrap">${escapeHtml(content)}</div>`;
    }

    // ✅ [A안] dataset.originMid 속성 추가 (선행 공백 포함하여 속성 분리 보장)
    const originMidAttr = (role === 'assistant' && originMid) ? ` data-origin-mid="${String(originMid)}"` : '';
    // ✅ [Edit] dataset.messageId 속성 추가 (서버 동기화용)
    const messageIdAttr = messageId ? ` data-message-id="${messageId}"` : '';
    // ✅ [FIX-1A] data-mid, data-role 추가 (Abort 메시지 Regenerate 지원)
    const midAttr = messageId !== null ? ` data-mid="${messageId}"` : ' data-mid="null"';
    const roleAttr = ` data-role="${role}"`;
    let html = `<div class="max-w-[85%] rounded-xl px-4 py-3 ${role==='user'?'message-user text-white':'message-assistant text-gray-100 shadow-lg'} relative group"${originMidAttr}${messageIdAttr}${midAttr}${roleAttr}>`;

    // ========================================
    // [NEW] 사용자 메시지: 수정 버튼 추가 (좌측 상단으로 이동)
    // ========================================
    if (role === 'user') {
        html += `
            <button onclick="startEditMessage(this)"
                    class="absolute top-2 -left-7 opacity-0 group-hover:opacity-100 transition-opacity p-1.5 bg-black bg-opacity-30 hover:bg-opacity-50 rounded-lg text-white shadow-lg"
                    title="Edit Message">
                <i class="fas fa-edit text-sm"></i>
            </button>
        `;
    }

    html += processed;

    // ========================================
    // Assistant 메시지: 인채팅 TaskBlock (User View) + 기존 피드백 버튼 유지
    // ========================================
    if(role==='assistant') {
        if (taskBlockState && typeof window.buildTaskBlockHTML === 'function') {
            html += window.buildTaskBlockHTML(taskBlockState);
        }
        html += `<div class="mt-2 flex flex-wrap gap-2 items-center">`;
        // [복구] 시간 표시 (서버 시간 또는 클라이언트 계산 시간)
        if(timeTaken) html += `<span class="py-1 px-2 bg-gray-800 bg-opacity-50 rounded text-[10px] text-gray-400 border border-gray-600 font-mono">⏱️ ${parseFloat(timeTaken).toFixed(2)}s</span>`;
        if(selectedMode) html += `<span class="py-1 px-2 bg-gray-700 bg-opacity-50 rounded text-[10px] border">🤖 ${autoSelected?"Auto→":""}${selectedMode.toUpperCase()}</span>`;
        if(ragUsed) html += `<span class="py-1 px-2 bg-purple-900 bg-opacity-50 rounded text-[10px] text-purple-200 border border-purple-700">📚 RAG</span>`;
        html += `</div>`;

        // 버튼들
        const upColor = feedbackPositive===true?'text-green-400':'text-gray-500';
        const downColor = feedbackPositive===false?'text-red-400':'text-gray-500';
        html += `<div class="flex items-center gap-3 mt-3 pt-2 border-t border-gray-700/50"><button onclick="copyToClipboard(this)" class="text-xs text-gray-500 hover:text-white" title="Copy Text"><i class="fas fa-copy"></i></button>`;
        if(messageId) html += `<div class="flex gap-2 border-l border-gray-700 pl-3"><button onclick="submitFeedback(${messageId},true,this)" class="text-xs ${upColor} hover:text-green-400"><i class="fas fa-thumbs-up"></i></button><button onclick="submitFeedback(${messageId},false,this)" class="text-xs ${downColor} hover:text-red-400"><i class="fas fa-thumbs-down"></i></button></div>`;
        // ... (위쪽 코드: 복사 버튼, 피드백 버튼 등) ...
        
        // 1. 💎 버튼을 담을 '우측 정렬 그릇' (하나만 만든다!)
        html += `<div class="ml-auto flex gap-2">`;

        // 🔒 2. Regenerate 버튼: 소설방(is_creative) VIP 전용
        // (불필요한 div 태그 제거하고 button만 깔끔하게 넣음)
        if (CURRENT_FOLDER && CURRENT_FOLDER.is_creative) {
             html += `<button onclick="regenerateResponse(${messageId}, this)" class="btn-regenerate text-xs text-gray-500 hover:text-purple-400" title="Regenerate" style="display:none;"><i class="fas fa-sync-alt"></i></button>`;
        }
        // 🌍 3. Continue 버튼: 누구나 사용 가능 (조건 없음)
        html += `<button onclick="continueResponse(${messageId}, this)" class="btn-continue text-xs text-gray-500 hover:text-blue-400" title="Continue" style="display:none;"><i class="fas fa-arrow-right"></i></button>`;
        // 4. 그릇 닫기
        html += `</div>`;
    }
    html += `</div>`;
    div.innerHTML = html;
    container.appendChild(div);

    // ✅ [Dev Agent Console] patch_report JSON이면 구조화 카드 렌더 + 접힌 상세에 evolution 원문
    if (patchReportData) {
        const patchEl = div.querySelector('.patch-report-container');
        if (patchEl && typeof renderPatchReport === 'function') renderPatchReport(patchReportData, patchEl);
        const evolutionPayloadStr = evolutionPayload && (typeof evolutionPayload === 'string' ? evolutionPayload.trim() : null);
        if (evolutionPayloadStr) {
            const evContainer = div.querySelector('.evolution-payload-container');
            if (evContainer) {
                try {
                    const evParsed = JSON.parse(evolutionPayloadStr);
                    if (evParsed && evParsed.type === 'evolution_report' && typeof adaptEvolutionContractToApiShape === 'function' && typeof renderEvolutionReport === 'function') {
                        const adapted = adaptEvolutionContractToApiShape(evParsed);
                        renderEvolutionReport(adapted, evContainer);
                    } else {
                        evContainer.innerHTML = '<pre class="text-xs text-slate-400 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap">' + escapeHtml(evolutionPayloadStr.slice(0, 8000)) + (evolutionPayloadStr.length > 8000 ? '\n...' : '') + '</pre>';
                    }
                } catch (_) {
                    evContainer.innerHTML = '<pre class="text-xs text-slate-400 overflow-x-auto max-h-60 overflow-y-auto whitespace-pre-wrap">' + escapeHtml(evolutionPayloadStr.slice(0, 4000)) + '</pre>';
                }
            }
        }
    }
    // ✅ [Autonomous Mode] task_block JSON이면 구조화 카드 렌더
    if (taskBlockReportData) {
        const tbEl = div.querySelector('.autonomous-task-block-container');
        if (tbEl && typeof renderAutonomousTaskBlock === 'function') renderAutonomousTaskBlock(taskBlockReportData, tbEl);
    }
    // ✅ [Evolution Mode] evolution_report JSON이면 삼권분립 카드로 렌더 (contract → API shape 변환)
    if (evolutionReportData) {
        const evEl = div.querySelector('.evolution-report-container');
        if (evEl && typeof renderEvolutionReport === 'function') {
            const adapted = adaptEvolutionContractToApiShape(evolutionReportData);
            renderEvolutionReport(adapted, evEl);
        }
    }

    // ✅ [Plan Card] 세션 로드 시 복원: 계획 카드 + 진행하기 버튼
    if (role === 'assistant' && planCard && Array.isArray(planCard.todos) && planCard.todos.length > 0 && (planCard.user_input || '').trim()) {
        const bubble = div.querySelector('.message-assistant');
        if (bubble && !bubble.querySelector('.plan-approval-card')) {
            const listHtml = planCard.todos.map(function (t) {
                const title = (t.title || t.todo_id || t.id || '').toString();
                return '<li class="flex items-center gap-2 text-sm text-slate-300">○ ' + (typeof escapeHtml === 'function' ? escapeHtml(title) : title.replace(/</g, '&lt;')) + '</li>';
            }).join('');
            const wrap = document.createElement('div');
            wrap.className = 'plan-approval-card-wrap mt-3';
            wrap.innerHTML = '<div class="plan-approval-card p-4 rounded-xl border border-blue-500/30 bg-blue-900/10 shadow-lg">' +
                '<div class="text-xs font-semibold text-blue-400 mb-2">📋 계획</div>' +
                '<ul class="space-y-1 list-none pl-0 mb-4">' + listHtml + '</ul>' +
                '<button type="button" class="plan-execute-btn px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium transition-colors">진행하기</button>' +
                '</div>';
            const btn = wrap.querySelector('.plan-execute-btn');
            if (btn && typeof sendMessage === 'function') {
                const userInput = (planCard.user_input || '').trim();
                btn.onclick = function () {
                    if (!userInput) return;
                    btn.disabled = true;
                    btn.textContent = '실행 중...';
                    sendMessage(userInput, { plan_approved: true, skipAddUserMessage: true });
                };
            }
            bubble.appendChild(wrap);
        }
    }

    // ✅ [Regenerate] assistant 메시지 추가 후 버튼 가시성 업데이트
    if (role === 'assistant') {
        setTimeout(() => updateRegenerateVisibility({ showOnly: div }), 0);
    }
}

/**
 * Regenerate 버튼 가시성 제어
 * @param {Object} options - { hideAll: boolean, showOnly: HTMLElement }
 */
function updateRegenerateVisibility(options = {}) {
    const { hideAll = false, showOnly = null } = options;

    // 모든 버튼 찾기
    const allRegenerateButtons = document.querySelectorAll('.btn-regenerate');
    const allContinueButtons = document.querySelectorAll('.btn-continue');

    if (hideAll) {
        // 모두 숨김
        allRegenerateButtons.forEach(btn => btn.style.display = 'none');
        allContinueButtons.forEach(btn => btn.style.display = 'none');
        return;
    }

    if (showOnly) {
        // 1. 모든 버튼 숨김
        allRegenerateButtons.forEach(btn => btn.style.display = 'none');
        allContinueButtons.forEach(btn => btn.style.display = 'none');

        // 2. 지정된 버블의 regenerate 버튼만 표시
        const targetButton = showOnly.querySelector('.btn-regenerate');
        if (targetButton) {
            targetButton.style.display = 'inline-block';
        }
        const targetContinueBtn = showOnly.querySelector('.btn-continue');
        if (targetContinueBtn) {
            targetContinueBtn.style.display = 'inline-block';
        }
    }
}

// [중요] 스마트 카피 기능 복구 (아이콘 변경 + 내용만 복사)
async function copyToClipboard(btn) {
    try {
        const messageBubble = btn.closest('[data-role="assistant"]') || btn.closest('.message-assistant'); if (!messageBubble) return;

        let txt = "";
        // 1. 답변만 추출 (생각 과정 제외)
        const actualAnswerDiv = messageBubble.querySelector('.actual-answer');
        if (actualAnswerDiv) txt = actualAnswerDiv.innerText;
        else {
            const simpleTextDiv = messageBubble.querySelector('.whitespace-pre-wrap');
            if(simpleTextDiv) txt = simpleTextDiv.innerText;
            else txt = messageBubble.innerText; // Fallback
        }

        await navigator.clipboard.writeText(txt);
        
        // 2. 아이콘 변경 애니메이션
        const icon = btn.querySelector('i');
        const originalClass = icon.className;
        icon.className = 'fas fa-check text-green-400';
        setTimeout(() => icon.className = originalClass, 2000);
    } catch(e) { console.error(e); alert('Copy failed'); }
}

/**
 * 버전 관리 (간소화)
 */
function findBubbleByOriginMid(originMid) {
    return document.querySelector(`[data-origin-mid="${originMid}"]`);
}

function attachOrUpdateVersionControls(bubbleEl, originMid) {
    if (!State.getAnswerVersions()[originMid] || State.getAnswerVersions()[originMid].items.length <= 1) {
        // 버전이 1개 이하면 토글 UI 숨김
        const existingControls = bubbleEl.querySelector('.version-controls');
        if (existingControls) existingControls.remove();
        return;
    }

    const versions = State.getAnswerVersions()[originMid];
    const currentIndex = versions.index;
    const totalCount = versions.items.length;

    // 기존 컨트롤 제거 (업데이트용)
    const existingControls = bubbleEl.querySelector('.version-controls');
    if (existingControls) existingControls.remove();

    // content div 찾기 (actual-answer 또는 whitespace-pre-wrap)
    const contentDiv = bubbleEl.querySelector('.actual-answer') || bubbleEl.querySelector('.whitespace-pre-wrap');
    if (!contentDiv) {
        console.warn(`⚠️ [Version] Could not find content div for originMid=${originMid}`);
        return;
    }

    // 버전 토글 UI 생성
    const controlsDiv = document.createElement('div');
    controlsDiv.className = 'version-controls mt-3 pt-3 border-t border-gray-700 flex items-center justify-between text-xs text-gray-400';
    controlsDiv.innerHTML = `
        <button class="version-prev px-2 py-1 rounded hover:bg-gray-700 transition ${currentIndex === 0 ? 'opacity-30 cursor-not-allowed' : ''}"
                ${currentIndex === 0 ? 'disabled' : ''}
                onclick="switchVersion('${originMid}', -1)">
            <i class="fas fa-chevron-left"></i>
        </button>
        <span class="version-counter font-mono">${currentIndex + 1} / ${totalCount}</span>
        <button class="version-next px-2 py-1 rounded hover:bg-gray-700 transition ${currentIndex === totalCount - 1 ? 'opacity-30 cursor-not-allowed' : ''}"
                ${currentIndex === totalCount - 1 ? 'disabled' : ''}
                onclick="switchVersion('${originMid}', 1)">
            <i class="fas fa-chevron-right"></i>
        </button>
    `;

    // 버블 끝에 추가
    bubbleEl.appendChild(controlsDiv);
    console.log(`🔁 [Version] Attached controls for originMid=${originMid} (${currentIndex + 1}/${totalCount})`);
}

function switchVersion(originMid, direction) {
    const versions = State.getAnswerVersions()[originMid];
    if (!versions) return;

    // 인덱스 변경
    const newIndex = versions.index + direction;
    if (newIndex < 0 || newIndex >= versions.items.length) return;

    versions.index = newIndex;
    const versionData = versions.items[newIndex];

    console.log(`🔁 [Version] originMid=${originMid} -> ${newIndex + 1}/${versions.items.length}`);

    // 버블 찾기
    const bubbleEl = findBubbleByOriginMid(originMid);
    if (!bubbleEl) {
        console.warn(`⚠️ [Version] Bubble not found for originMid=${originMid}`);
        return;
    }

    // content div 찾기
    const contentDiv = bubbleEl.querySelector('.actual-answer') || bubbleEl.querySelector('.whitespace-pre-wrap');
    if (!contentDiv) {
        console.warn(`⚠️ [Version] Content div not found for originMid=${originMid}`);
        return;
    }

    // <think> 태그 처리
    let displayContent = versionData.content;
    if (displayContent.includes('<think>')) {
        const parts = displayContent.split('</think>');
        const thinkContent = parts[0].replace('<think>', '').trim();
        const answerContent = parts[1] ? parts[1].trim() : '';

        // 부모 버블에서 details와 actual-answer를 모두 업데이트
        const thoughtDetails = bubbleEl.querySelector('details');
        if (thoughtDetails) {
            const thoughtContentDiv = thoughtDetails.querySelector('.thought-content');
            if (thoughtContentDiv) thoughtContentDiv.textContent = thinkContent;
        }
        contentDiv.textContent = answerContent;
    } else {
        contentDiv.textContent = displayContent;
    }

    // 토글 UI 업데이트
    attachOrUpdateVersionControls(bubbleEl, originMid);
}


/**
 * 사이드바 토글
 */
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('sidebarToggleBtn');
    const toggleIcon = document.getElementById('toggleIcon');

    SIDEBAR_COLLAPSED = !SIDEBAR_COLLAPSED;

    if (SIDEBAR_COLLAPSED) {
        // Collapse sidebar
        sidebar.className = 'sidebar-collapsed bg-dark-card border-l border-dark-border transition-all duration-300 flex flex-col overflow-hidden';
        // Show floating toggle button
        if (toggleBtn) {
            toggleBtn.style.display = 'block';
            toggleBtn.innerHTML = '◀';
            toggleBtn.title = 'Open Chat History';
        }
    } else {
        // Expand sidebar
        sidebar.className = 'sidebar-expanded bg-dark-card border-l border-dark-border transition-all duration-300 flex flex-col overflow-hidden';
        // Hide floating toggle button
        if (toggleBtn) {
            toggleBtn.style.display = 'none';
        }
    }

    // Update icon inside sidebar header
    if (toggleIcon) {
        toggleIcon.textContent = SIDEBAR_COLLAPSED ? '‹' : '›';
    }
}

// =============================================================================
// Mission B: VRAM Monitoring Widget
// =============================================================================

/**
 * VRAM 위젯 업데이트
 */
function updateVRAMWidget() {
    const widget = document.getElementById('vramWidget');
    if (!widget) return;

    const { used, total, percent } = window.VRAM_STATUS;
    const usedGB = (used / 1024).toFixed(1);
    const totalGB = (total / 1024).toFixed(1);

    // 게이지 바 색상 결정 (80% 이상이면 경고색)
    let barColor = 'bg-purple-500';
    let textColor = 'text-purple-400';
    if (percent >= 80) {
        barColor = 'bg-red-500';
        textColor = 'text-red-400';
    } else if (percent >= 60) {
        barColor = 'bg-orange-500';
        textColor = 'text-orange-400';
    }

    widget.innerHTML = `
        <div class="flex items-center gap-2">
            <i class="fas fa-microchip ${textColor}"></i>
            <div class="flex flex-col">
                <div class="flex items-center gap-1">
                    <span class="text-xs text-gray-400">VRAM</span>
                    <span class="text-xs ${textColor} font-mono">${usedGB}/${totalGB}GB</span>
                </div>
                <div class="w-20 h-1.5 bg-dark-border rounded-full overflow-hidden">
                    <div class="${barColor} h-full transition-all duration-300 rounded-full" style="width: ${Math.min(percent, 100)}%"></div>
                </div>
            </div>
        </div>
    `;
}

// =============================================================================
// Mission A: Multimodal Image Rendering
// =============================================================================

/**
 * 이미지 생성 로딩 스피너 추가
 */
function addImageLoadingIndicator(container) {
    const loadingDiv = document.createElement('div');
    loadingDiv.id = 'imageGenerationLoading';
    loadingDiv.className = 'flex justify-start mb-4';
    loadingDiv.innerHTML = `
        <div class="max-w-[85%] rounded-xl px-4 py-4 message-assistant text-gray-100 shadow-lg">
            <div class="flex items-center gap-3">
                <div class="relative">
                    <div class="w-8 h-8 border-4 border-purple-500 border-t-transparent rounded-full animate-spin"></div>
                    <i class="fas fa-image absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 text-purple-400 text-xs"></i>
                </div>
                <div>
                    <p class="text-sm font-medium text-purple-300">이미지 생성 중...</p>
                    <p class="text-xs text-gray-500">Flux 모델이 작업 중입니다</p>
                </div>
            </div>
            <div class="mt-3 w-full bg-dark-border rounded-full h-1.5 overflow-hidden">
                <div class="bg-gradient-to-r from-purple-500 to-pink-500 h-full animate-pulse" style="width: 60%"></div>
            </div>
        </div>
    `;
    container.appendChild(loadingDiv);
    container.scrollTop = container.scrollHeight;
    return loadingDiv;
}

/**
 * 이미지 생성 로딩 제거
 */
function removeImageLoadingIndicator() {
    const loading = document.getElementById('imageGenerationLoading');
    if (loading) loading.remove();
}

/**
 * 이미지 메시지 렌더링 (멀티모달)
 * @param {string} imageUrl - 생성된 이미지 URL
 * @param {string} textContent - 텍스트 응답 (있는 경우)
 * @param {object} metadata - 메타데이터 (intent, refined_prompt 등)
 */
function addImageMessageToUI(imageUrl, textContent = '', metadata = {}) {
    const container = document.getElementById('chatMessages');
    removeImageLoadingIndicator();

    const div = document.createElement('div');
    div.className = 'flex justify-start mb-4';

    const { intent, refined_prompt, processing_time, message_id } = metadata;

    // 인텐트 배지 생성
    const intentBadge = getIntentBadge(intent || 'image_request');

    // Prompt Details 아코디언 (refined_prompt가 있는 경우)
    let promptDetailsHtml = '';
    if (refined_prompt) {
        promptDetailsHtml = `
            <details class="mt-3 border border-gray-700 rounded-lg overflow-hidden">
                <summary class="cursor-pointer px-3 py-2 bg-dark-hover hover:bg-dark-border transition text-xs text-gray-400 flex items-center gap-2">
                    <i class="fas fa-magic text-purple-400"></i>
                    <span>Show Prompt Details</span>
                </summary>
                <div class="px-3 py-2 bg-black bg-opacity-30 text-xs text-gray-300 font-mono whitespace-pre-wrap">${escapeHtml(refined_prompt)}</div>
            </details>
        `;
    }

    // 시간 표시
    const timeHtml = processing_time
        ? `<span class="py-1 px-2 bg-gray-800 bg-opacity-50 rounded text-[10px] text-gray-400 border border-gray-600 font-mono">⏱️ ${parseFloat(processing_time).toFixed(2)}s</span>`
        : '';

    div.innerHTML = `
        <div class="max-w-[85%] rounded-xl px-4 py-3 message-assistant text-gray-100 shadow-lg relative group" data-message-id="${message_id || ''}">
            <!-- Intent Badge -->
            <div class="mb-2">${intentBadge}</div>

            <!-- Text Content (있는 경우) -->
            ${textContent ? `<div class="actual-answer whitespace-pre-wrap mb-3">${escapeHtml(textContent)}</div>` : ''}

            <!-- Generated Image -->
            <div class="generated-image-container rounded-lg overflow-hidden border border-gray-700 bg-black">
                <img src="${imageUrl}"
                     alt="Generated Image"
                     class="w-full max-w-lg object-contain cursor-pointer hover:opacity-90 transition"
                     style="max-height: 512px; aspect-ratio: auto;"
                     onclick="openImageModal('${imageUrl}')"
                     loading="lazy"
                />
            </div>

            <!-- Prompt Details Accordion -->
            ${promptDetailsHtml}

            <!-- Metadata Row -->
            <div class="mt-3 flex flex-wrap gap-2 items-center">
                ${timeHtml}
                <button onclick="downloadImage('${imageUrl}')" class="text-xs text-gray-500 hover:text-white px-2 py-1 bg-dark-hover rounded" title="Download">
                    <i class="fas fa-download"></i>
                </button>
                <button onclick="copyToClipboard(this)" class="text-xs text-gray-500 hover:text-white px-2 py-1 bg-dark-hover rounded" title="Copy Prompt">
                    <i class="fas fa-copy"></i>
                </button>
            </div>
        </div>
    `;

    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

/**
 * 이미지 모달 열기 (고해상도 미리보기)
 */
function openImageModal(imageUrl) {
    // 기존 모달 제거
    const existingModal = document.getElementById('imagePreviewModal');
    if (existingModal) existingModal.remove();

    const modal = document.createElement('div');
    modal.id = 'imagePreviewModal';
    modal.className = 'fixed inset-0 bg-black bg-opacity-90 flex items-center justify-center z-[100] cursor-pointer';
    modal.onclick = () => modal.remove();
    modal.innerHTML = `
        <div class="relative max-w-[90vw] max-h-[90vh]">
            <img src="${imageUrl}" alt="Preview" class="max-w-full max-h-[90vh] object-contain rounded-lg shadow-2xl">
            <button onclick="event.stopPropagation(); document.getElementById('imagePreviewModal').remove()"
                    class="absolute top-2 right-2 w-10 h-10 bg-black bg-opacity-50 hover:bg-opacity-70 rounded-full flex items-center justify-center text-white transition">
                <i class="fas fa-times"></i>
            </button>
            <div class="absolute bottom-2 right-2 flex gap-2">
                <a href="${imageUrl}" download class="px-4 py-2 bg-purple-600 hover:bg-purple-700 rounded-lg text-white text-sm transition" onclick="event.stopPropagation()">
                    <i class="fas fa-download mr-1"></i> Download
                </a>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
}

/**
 * 이미지 다운로드
 */
async function downloadImage(imageUrl) {
    try {
        const response = await fetch(imageUrl);
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `generated_${Date.now()}.png`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } catch (e) {
        console.error('[Download] Failed:', e);
        // Fallback: 새 탭에서 열기
        window.open(imageUrl, '_blank');
    }
}

// =============================================================================
// Mission C: Intent Badges
// =============================================================================

/**
 * 인텐트에 따른 배지 HTML 생성
 * @param {string} intent - simple_chat, image_request, document_qa
 */
function getIntentBadge(intent) {
    const badges = {
        'image_request': `<span class="inline-flex items-center gap-1 px-2 py-0.5 bg-pink-900 bg-opacity-50 text-pink-300 text-[10px] rounded-full border border-pink-700"><i class="fas fa-palette"></i> Image</span>`,
        'document_qa': `<span class="inline-flex items-center gap-1 px-2 py-0.5 bg-blue-900 bg-opacity-50 text-blue-300 text-[10px] rounded-full border border-blue-700"><i class="fas fa-book"></i> Document</span>`,
        'simple_chat': `<span class="inline-flex items-center gap-1 px-2 py-0.5 bg-gray-700 bg-opacity-50 text-gray-300 text-[10px] rounded-full border border-gray-600"><i class="fas fa-comment"></i> Chat</span>`
    };
    return badges[intent] || badges['simple_chat'];
}

/**
 * 기존 addMessageToUI 확장 - 인텐트 배지 지원
 * (이 함수는 기존 addMessageToUI를 호출한 후 배지를 추가합니다)
 */
function addMessageWithIntent(role, content, metadata = {}) {
    const { intent, refined_prompt, image_url, target_service } = metadata;

    // 이미지 응답인 경우
    if (target_service === 'image' || image_url) {
        addImageMessageToUI(image_url, content, metadata);
        return;
    }

    // 일반 텍스트 응답 (기존 addMessageToUI 사용 후 배지 삽입)
    addMessageToUI(
        role,
        content,
        metadata.rag_used,
        metadata.message_id,
        metadata.feedback_positive,
        metadata.auto_selected,
        metadata.selected_mode,
        metadata.processing_time,
        metadata.origin_mid
    );

    // 인텐트 배지 삽입 (마지막 assistant 메시지에)
    if (role === 'assistant' && intent) {
        setTimeout(() => {
            const lastBubble = document.querySelector('.message-assistant:last-of-type');
            if (lastBubble) {
                const badgeHtml = getIntentBadge(intent);
                const existingBadge = lastBubble.querySelector('.intent-badge');
                if (!existingBadge) {
                    const badgeContainer = document.createElement('div');
                    badgeContainer.className = 'intent-badge mb-2';
                    badgeContainer.innerHTML = badgeHtml;
                    lastBubble.insertBefore(badgeContainer, lastBubble.firstChild);
                }

                // Prompt Details 추가 (이미지 아닌 경우에도 refined_prompt가 있으면)
                if (refined_prompt && !lastBubble.querySelector('.prompt-details')) {
                    const detailsHtml = `
                        <details class="prompt-details mt-3 border border-gray-700 rounded-lg overflow-hidden">
                            <summary class="cursor-pointer px-3 py-2 bg-dark-hover hover:bg-dark-border transition text-xs text-gray-400 flex items-center gap-2">
                                <i class="fas fa-magic text-purple-400"></i>
                                <span>Show Details</span>
                            </summary>
                            <div class="px-3 py-2 bg-black bg-opacity-30 text-xs text-gray-300 font-mono whitespace-pre-wrap">${escapeHtml(refined_prompt)}</div>
                        </details>
                    `;
                    const metaRow = lastBubble.querySelector('.flex.flex-wrap.gap-2');
                    if (metaRow) {
                        metaRow.insertAdjacentHTML('beforebegin', detailsHtml);
                    }
                }
            }
        }, 50);
    }
}

// =========================
// Patch Report Card (Dev Agent Console — Reasoned Patch Report)
// type === "patch_report" 인 assistant 메시지를 구조화 카드로 렌더링
// =========================

/**
 * Dev Agent Console Patch Report JSON을 카드로 렌더링.
 * @param {Object} data - { type, status, summary, issues[], changed_files[], regression_guard[], confidence, risk_level }
 * @param {HTMLElement|null} targetElement - 있으면 여기에 렌더, 없으면 chatMessages에 새 div 추가
 */
function renderPatchReport(data, targetElement) {
    const status = (data.status || 'partial').toString();
    const statusClass = status === 'applied' ? 'border-green-500/50' : status === 'rejected' ? 'border-red-500/50' : 'border-amber-500/50';
    const summary = escapeHtml((data.summary || '').toString());
    const issues = Array.isArray(data.issues) ? data.issues : [];
    const changedFiles = Array.isArray(data.changed_files) ? data.changed_files : [];
    const guards = Array.isArray(data.regression_guard) ? data.regression_guard : [];
    const confidence = (data.confidence || 'medium').toString();
    const riskLevel = typeof data.risk_level === 'number' ? data.risk_level : 2;

    const issuesHtml = issues.length
        ? issues.map(function (iss, i) {
            const t = escapeHtml((iss.title || 'Issue ' + (i + 1)).toString());
            const cause = escapeHtml((iss.cause || '').toString());
            const fix = escapeHtml((iss.fix || '').toString());
            const impact = escapeHtml((iss.impact || '').toString());
            const verification = escapeHtml((iss.verification || '').toString());
            return '<details class="border border-gray-700 rounded-lg overflow-hidden"><summary class="cursor-pointer px-3 py-2 bg-slate-800/50 hover:bg-slate-700/50 transition flex items-center gap-2 text-sm">' + t + '</summary><div class="px-3 py-2 bg-black/30 text-gray-300 text-xs space-y-2"><div><span class="text-gray-500">Cause:</span> ' + cause + '</div><div><span class="text-gray-500">Fix:</span> ' + fix + '</div><div><span class="text-gray-500">Impact:</span> ' + impact + '</div><div><span class="text-gray-500">Verification:</span> ' + verification + '</div></div></details>';
        }).join('')
        : '<p class="text-gray-500 text-xs">(없음)</p>';

    const filesHtml = changedFiles.length
        ? '<ul class="list-none pl-0 space-y-1 text-xs font-mono text-slate-400">' + changedFiles.map(function (f) {
            const path = escapeHtml((f.path || '').toString());
            const diff = escapeHtml((f.diff_summary || 'unknown').toString());
            return '<li><span class="text-blue-300">' + path + '</span> <span class="text-gray-500">' + diff + '</span></li>';
        }).join('') + '</ul>'
        : '<p class="text-gray-500 text-xs">(없음)</p>';

    const guardsHtml = guards.length
        ? '<ul class="list-none pl-0 space-y-0.5 text-xs text-amber-200/90">' + guards.map(function (g) { return '<li>• ' + escapeHtml(String(g)) + '</li>'; }).join('') + '</ul>'
        : '<p class="text-gray-500 text-xs">(없음)</p>';

    const cardHtml = '<div class="patch-report-card rounded-xl p-4 bg-slate-900/80 border-2 ' + statusClass + ' shadow-xl">' +
        '<div class="flex items-center justify-between mb-3"><h3 class="text-sm font-bold text-slate-200 flex items-center gap-2"><i class="fas fa-code-branch text-blue-400"></i> Patch Report</h3>' +
        '<span class="text-xs px-2 py-1 rounded ' + (status === 'applied' ? 'bg-green-900/50 text-green-300' : status === 'rejected' ? 'bg-red-900/50 text-red-300' : 'bg-amber-900/50 text-amber-300') + '">' + escapeHtml(status) + '</span></div>' +
        '<p class="text-sm text-slate-300 mb-3">' + summary + '</p>' +
        '<details class="mb-2"><summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-300">Issues (' + issues.length + ')</summary><div class="mt-2 space-y-2">' + issuesHtml + '</div></details>' +
        '<details class="mb-2"><summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-300">Changed files (' + changedFiles.length + ')</summary><div class="mt-2">' + filesHtml + '</div></details>' +
        '<details class="mb-2"><summary class="cursor-pointer text-xs text-slate-400 hover:text-slate-300">Regression guard</summary><div class="mt-2">' + guardsHtml + '</div></details>' +
        '<div class="flex gap-2 mt-2 text-xs text-slate-500">Confidence: ' + escapeHtml(confidence) + ' · Risk: ' + riskLevel + '</div>' +
        '</div>';

    if (targetElement) {
        targetElement.innerHTML = cardHtml;
        return;
    }
    const container = document.getElementById('chatMessages');
    if (!container) return;
    const wrap = document.createElement('div');
    wrap.className = 'flex justify-start mb-4';
    wrap.innerHTML = '<div class="max-w-[90%]">' + cardHtml + '</div>';
    container.appendChild(wrap);
}

// =========================
// Autonomous TaskBlock Card (Autonomous Mode — task_block contract)
// type === "task_block" 인 assistant 메시지를 구조화 카드로 렌더링
// =========================

/**
 * Autonomous Mode task_block JSON을 카드로 렌더링.
 * @param {Object} data - { type, status, task_id, title, phase, progress, steps[], next_action, requires_human_action, risk_level, confidence }
 * @param {HTMLElement|null} targetElement - 있으면 여기에 렌더, 없으면 chatMessages에 추가
 */
function renderAutonomousTaskBlock(data, targetElement) {
    const status = (data.status || 'running').toString();
    const statusClass = status === 'completed' ? 'border-green-500/50' : status === 'failed' ? 'border-red-500/50' : status === 'waiting_approval' ? 'border-amber-500/50' : 'border-blue-500/50';
    const title = escapeHtml((data.title || 'Task').toString());
    const taskId = escapeHtml((data.task_id || '').toString().slice(0, 24));
    const phase = escapeHtml((data.phase || 'execution').toString());
    const progress = data.progress || {};
    const currentStep = typeof progress.current_step === 'number' ? progress.current_step : 0;
    const totalSteps = typeof progress.total_steps === 'number' ? progress.total_steps : 0;
    const pct = totalSteps > 0 ? Math.round((currentStep / totalSteps) * 100) : 0;
    const steps = Array.isArray(data.steps) ? data.steps : [];
    const nextAction = escapeHtml((data.next_action || '').toString());
    const humanRequired = !!data.requires_human_action;
    const riskLevel = typeof data.risk_level === 'number' ? data.risk_level : 2;
    const confidence = escapeHtml((data.confidence || 'medium').toString());

    const stepsHtml = steps.length
        ? steps.map(function (s) {
            const st = (s.state || 'pending').toString();
            const rowClass = st === 'done' ? 'text-green-400' : st === 'failed' ? 'text-red-400' : st === 'in_progress' ? 'text-blue-400' : 'text-slate-500';
            const desc = escapeHtml((s.description || '').toString());
            const tool = s.tool_used ? escapeHtml(s.tool_used) : '-';
            const result = s.result ? escapeHtml(s.result.toString().slice(0, 80)) : '-';
            return '<tr class="border-b border-gray-700/50 ' + rowClass + '"><td class="py-1 pr-2 text-xs font-mono">' + (s.step_id ?? '') + '</td><td class="py-1 pr-2 text-xs">' + desc + '</td><td class="py-1 pr-2 text-xs font-mono">' + tool + '</td><td class="py-1 pr-2 text-xs opacity-80">' + result + '</td><td class="py-1 text-xs">' + escapeHtml(st) + '</td></tr>';
        }).join('')
        : '';

    const cardHtml = '<div class="autonomous-task-block-card rounded-xl p-4 bg-slate-900/80 border-2 ' + statusClass + ' shadow-xl">' +
        '<div class="flex items-center justify-between mb-3"><h3 class="text-sm font-bold text-slate-200 flex items-center gap-2"><i class="fas fa-cogs text-blue-400"></i> Task Block</h3>' +
        '<span class="text-xs px-2 py-1 rounded ' + (status === 'completed' ? 'bg-green-900/50 text-green-300' : status === 'failed' ? 'bg-red-900/50 text-red-300' : status === 'waiting_approval' ? 'bg-amber-900/50 text-amber-300' : 'bg-blue-900/50 text-blue-300') + '">' + escapeHtml(status) + '</span></div>' +
        '<p class="text-sm text-slate-300 mb-1">' + title + '</p>' +
        '<div class="text-xs text-slate-500 mb-2 font-mono">' + taskId + ' · ' + phase + '</div>' +
        '<div class="flex items-center gap-2 mb-3 text-xs"><div class="flex-1 h-1.5 bg-gray-700 rounded overflow-hidden"><div class="h-full bg-blue-500 rounded transition-all" style="width:' + pct + '%"></div></div><span class="text-slate-400">' + currentStep + '/' + totalSteps + ' (' + pct + '%)</span></div>' +
        (steps.length ? '<table class="w-full text-xs mb-3"><thead><tr class="text-slate-500 border-b border-gray-700"><th class="text-left py-1 pr-2">#</th><th class="text-left py-1 pr-2">Description</th><th class="text-left py-1 pr-2">Tool</th><th class="text-left py-1 pr-2">Result</th><th class="text-left py-1">State</th></tr></thead><tbody>' + stepsHtml + '</tbody></table>' : '') +
        '<div class="text-xs text-slate-400 mb-1"><span class="text-slate-500">Next:</span> ' + nextAction + '</div>' +
        (humanRequired ? '<div class="text-xs text-amber-400">⚠️ Human action required</div>' : '') +
        '<div class="flex gap-2 mt-2 text-xs text-slate-500">Confidence: ' + confidence + ' · Risk: ' + riskLevel + '</div>' +
        '</div>';

    if (targetElement) {
        targetElement.innerHTML = cardHtml;
        return;
    }
    const container = document.getElementById('chatMessages');
    if (!container) return;
    const wrap = document.createElement('div');
    wrap.className = 'flex justify-start mb-4';
    wrap.innerHTML = '<div class="max-w-[90%]">' + cardHtml + '</div>';
    container.appendChild(wrap);
}

// =========================
// Evolution Report Card (삼권분립 결재 보고서)
// Mellow_Link_Spec.md Step 5 - [관제/판결/검수] 결과 카드 렌더링
// =========================

/**
 * Evolution Mode contract JSON을 기존 renderEvolutionReport API 형식으로 변환.
 * @param {Object} c - { proposal, tower, verdict, audit, apply, ... }
 * @returns {Object} - { id, user_request, tower_report, verdict_target_file, verdict_proposed_code, verdict_reason, audit_approved, audit_critique, audit_refined, error, created_at }
 */
function adaptEvolutionContractToApiShape(c) {
    const proposal = c.proposal || {};
    const tower = c.tower || {};
    const verdict = c.verdict || {};
    const audit = c.audit || {};
    const planLines = Array.isArray(verdict.plan) ? verdict.plan : [];
    const observations = Array.isArray(tower.observations) ? tower.observations : [];
    const rejectionReasons = Array.isArray(audit.rejection_reasons) ? audit.rejection_reasons : [];
    return {
        id: proposal.proposal_id || 'ev-inline-' + Date.now(),
        user_request: (proposal.user_request || '').toString(),
        tower_report: observations.join('\n'),
        verdict_target_file: proposal.target_file || '-',
        verdict_proposed_code: (verdict.diff_summary || '').toString() || null,
        verdict_reason: planLines.join('\n') || (verdict.diff_summary || '').toString(),
        audit_approved: !!audit.approved,
        audit_critique: rejectionReasons.join('\n') || (audit.notes || '').toString(),
        audit_refined: (audit.notes || '').toString(),
        error: null,
        created_at: null
    };
}

/**
 * 삼권분립 파이프라인 결과를 채팅창에 화려한 결재 보고서 카드로 렌더링.
 * @param {Object} data - run_evolution_cycle API 응답 또는 adaptEvolutionContractToApiShape 결과
 *   { id, user_request, tower_report, verdict_target_file, verdict_proposed_code, verdict_reason,
 *     audit_approved, audit_critique, audit_refined, error, created_at }
 * @param {HTMLElement|null} targetElement - 있으면 여기에 카드 추가, 없으면 chatMessages에 추가
 */
function renderEvolutionReport(data, targetElement) {
    const container = targetElement ? null : document.getElementById('chatMessages');
    if (!container && !targetElement) return;

    const pid = (data.id || '').toString().substring(0, 8);
    const adminOk = State.getIsAdmin();
    const approved = !!data.audit_approved;
    const statusClass = approved ? 'border-green-500/50' : 'border-amber-500/50';
    const statusIcon = approved ? '✅' : '⚠️';
    const statusText = approved ? '검수 승인' : '검토 필요';

    const towerReport = (data.tower_report || '').trim();
    const verdictTarget = data.verdict_target_file || '-';
    const verdictReason = (data.verdict_reason || '').trim();
    const verdictCode = (data.verdict_proposed_code || '').trim();
    const auditCritique = (data.audit_critique || '').trim();
    const auditRefined = (data.audit_refined || '').trim();
    const errMsg = data.error || '';

    const div = document.createElement('div');
    div.className = 'flex justify-start mb-4';
    div.id = `evolution-report-${data.id}`;
    div.dataset.proposalId = data.id;

    div.innerHTML = `
        <div class="max-w-[90%] rounded-xl p-4 bg-dark-card border-2 ${statusClass} shadow-xl">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-bold bg-gradient-to-r from-amber-400 to-orange-500 bg-clip-text text-transparent flex items-center gap-2">
                    <i class="fas fa-landmark"></i> 삼권분립 결재 보고서
                </h3>
                <span class="text-xs px-2 py-1 rounded ${approved ? 'bg-green-900/50 text-green-300' : 'bg-amber-900/50 text-amber-300'}">${statusIcon} ${statusText}</span>
            </div>
            <div class="text-xs text-gray-500 mb-3 font-mono">ID: ${escapeHtml(pid)} · ${escapeHtml(data.created_at || '')}</div>
            <div class="text-sm text-gray-300 mb-4">📋 ${escapeHtml((data.user_request || '').slice(0, 200))}${(data.user_request || '').length > 200 ? '...' : ''}</div>

            <div class="space-y-3 text-sm">
                <details class="border border-gray-700 rounded-lg overflow-hidden">
                    <summary class="cursor-pointer px-3 py-2 bg-purple-900/30 hover:bg-purple-900/50 transition flex items-center gap-2">
                        <i class="fas fa-tower-broadcast text-purple-400"></i> 관제 (Tower)
                    </summary>
                    <div class="px-3 py-2 bg-black/30 text-gray-300 whitespace-pre-wrap max-h-40 overflow-y-auto">${escapeHtml(towerReport) || '(없음)'}</div>
                </details>
                <details class="border border-gray-700 rounded-lg overflow-hidden">
                    <summary class="cursor-pointer px-3 py-2 bg-blue-900/30 hover:bg-blue-900/50 transition flex items-center gap-2">
                        <i class="fas fa-gavel text-blue-400"></i> 판결 (Verdict)
                    </summary>
                    <div class="px-3 py-2 bg-black/30 space-y-2">
                        <div><span class="text-gray-500">대상:</span> <code class="text-blue-300">${escapeHtml(verdictTarget)}</code></div>
                        <div><span class="text-gray-500">사유:</span> <span class="text-gray-300">${escapeHtml(verdictReason) || '-'}</span></div>
                        ${verdictCode ? `<pre class="text-xs overflow-x-auto max-h-32 overflow-y-auto bg-black/50 p-2 rounded">${escapeHtml(verdictCode)}</pre>` : ''}
                    </div>
                </details>
                <details class="border border-gray-700 rounded-lg overflow-hidden">
                    <summary class="cursor-pointer px-3 py-2 ${approved ? 'bg-green-900/30' : 'bg-amber-900/30'} hover:opacity-90 transition flex items-center gap-2">
                        <i class="fas fa-shield-halved ${approved ? 'text-green-400' : 'text-amber-400'}"></i> 검수 (Audit)
                    </summary>
                    <div class="px-3 py-2 bg-black/30 space-y-2 text-gray-300">
                        ${auditCritique ? `<div><span class="text-gray-500">검토:</span> ${escapeHtml(auditCritique)}</div>` : ''}
                        ${auditRefined ? `<div><span class="text-gray-500">보완안:</span> <pre class="text-xs whitespace-pre-wrap">${escapeHtml(auditRefined)}</pre></div>` : ''}
                    </div>
                </details>
            </div>

            ${errMsg ? `<div class="mt-3 text-red-400 text-xs">⚠️ ${escapeHtml(errMsg)}</div>` : ''}
            ${(data.cost_efficiency_briefing || '').trim() ? `<div class="mt-3 text-amber-300/90 text-xs">💰 ${escapeHtml(data.cost_efficiency_briefing)}</div>` : ''}

            <div class="flex gap-2 mt-4 pt-4 border-t border-gray-700">
                ${adminOk
                    ? `<button class="evolution-approve-btn flex-1 px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded-lg text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed" data-proposal-id="${escapeHtml(data.id)}" ${!approved ? 'disabled title="검수 미승인 시 적용 불가"' : ''}>승인</button>`
                    : `<button class="evolution-approve-btn flex-1 px-4 py-2 bg-gray-600 text-gray-400 rounded-lg text-sm cursor-not-allowed" disabled title="Admin 전용">승인 (Admin 전용)</button>`
                }
                <button onclick="document.getElementById('evolution-report-${data.id}').remove()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm transition">닫기</button>
            </div>
        </div>
    `;

    if (targetElement) {
        targetElement.appendChild(div);
    } else if (container) {
        container.appendChild(div);
    }

    const approveBtn = div.querySelector('.evolution-approve-btn');
    if (approveBtn && adminOk && approved) {
        approveBtn.onclick = () => applyEvolutionProposal(data.id);
    } else if (approveBtn && !adminOk) {
        approveBtn.onclick = () => typeof showAdminOnlyWarning === 'function' && showAdminOnlyWarning();
        approveBtn.removeAttribute('disabled');
        approveBtn.classList.remove('cursor-not-allowed');
    }
}

/**
 * Proposed Plan 대기 카드 (대규모 수정 시 계획 우선 보고)
 * 진행 승인 후 Verdict·Audit이 실행됨.
 */
function renderEvolutionPlanPending(data) {
    const container = document.getElementById('chatMessages');
    if (!container) return;

    const pid = (data.id || '').substring(0, 8);
    const adminOk = State.getIsAdmin();
    const towerReport = (data.tower_report || '').trim();

    const div = document.createElement('div');
    div.className = 'flex justify-start mb-4';
    div.id = `evolution-plan-pending-${data.id}`;
    div.dataset.proposalId = data.id;

    div.innerHTML = `
        <div class="max-w-[90%] rounded-xl p-4 bg-dark-card border-2 border-amber-500/50 shadow-xl">
            <div class="flex items-center justify-between mb-4">
                <h3 class="text-lg font-bold bg-gradient-to-r from-amber-400 to-orange-500 bg-clip-text text-transparent flex items-center gap-2">
                    <i class="fas fa-clipboard-list"></i> Proposed Plan (진행 승인 대기)
                </h3>
                <span class="text-xs px-2 py-1 rounded bg-amber-900/50 text-amber-300">📋 계획 보고됨</span>
            </div>
            <div class="text-xs text-gray-500 mb-3 font-mono">ID: ${escapeHtml(pid)} · ${escapeHtml(data.created_at || '')}</div>
            <div class="text-sm text-gray-300 mb-4">📋 ${escapeHtml((data.user_request || '').slice(0, 200))}${(data.user_request || '').length > 200 ? '...' : ''}</div>

            <details class="border border-gray-700 rounded-lg overflow-hidden mb-4" open>
                <summary class="cursor-pointer px-3 py-2 bg-purple-900/30 hover:bg-purple-900/50 transition flex items-center gap-2">
                    <i class="fas fa-tower-broadcast text-purple-400"></i> Tower 계획
                </summary>
                <div class="px-3 py-2 bg-black/30 text-gray-300 whitespace-pre-wrap max-h-40 overflow-y-auto">${escapeHtml(towerReport) || '(없음)'}</div>
            </details>

            <div class="flex gap-2 mt-4 pt-4 border-t border-gray-700">
                ${adminOk
                    ? `<button class="evolution-proceed-btn flex-1 px-4 py-2 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-sm font-medium transition">✅ 진행 승인</button>`
                    : `<button class="evolution-proceed-btn flex-1 px-4 py-2 bg-gray-600 text-gray-400 rounded-lg text-sm cursor-not-allowed" disabled title="Admin 전용">진행 승인 (Admin 전용)</button>`
                }
                <button onclick="document.getElementById('evolution-plan-pending-${data.id}').remove()" class="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg text-sm transition">닫기</button>
            </div>
        </div>
    `;

    container.appendChild(div);

    const proceedBtn = div.querySelector('.evolution-proceed-btn');
    if (proceedBtn && adminOk) {
        proceedBtn.onclick = () => proceedEvolutionFromPlan(data.id, proceedBtn);
    } else if (proceedBtn && !adminOk) {
        proceedBtn.onclick = () => typeof showAdminOnlyWarning === 'function' && showAdminOnlyWarning();
        proceedBtn.removeAttribute('disabled');
        proceedBtn.classList.remove('cursor-not-allowed');
    }
}

/**
 * proceed-from-plan API 호출 (진행 승인 버튼 클릭 시)
 */
async function proceedEvolutionFromPlan(proposalId, btnEl) {
    const adminOk = State.getIsAdmin();
    if (!adminOk) return;
    const baseUrl = (typeof API_BASE !== 'undefined') ? API_BASE : window.location.origin;
    const token = State.getAuthToken() || localStorage.getItem('auth_token');
    if (btnEl) {
        btnEl.disabled = true;
        btnEl.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 진행 중...';
    }
    try {
        const res = await fetch(`${baseUrl}/evolution/proceed-from-plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
            body: JSON.stringify({ proposal_id: proposalId })
        });
        const json = await res.json().catch(() => ({}));
        if (res.ok && json.id) {
            const card = document.getElementById(`evolution-plan-pending-${proposalId}`);
            if (card) card.remove();
            if (json.audit_approved) {
                if (typeof renderEvolutionReport === 'function') {
                    renderEvolutionReport(json);
                }
                if (typeof addMessageToUI === 'function') {
                    addMessageToUI('assistant', '✅ Verdict·Audit 완료. 결재 보고서가 생성되었습니다.', false, null, null, false, null, null);
                }
            } else if (json.error) {
                if (typeof addMessageToUI === 'function') {
                    addMessageToUI('assistant', `⚠️ 진행 실패: ${json.error}`, false, null, null, false, null, null);
                }
            }
        } else {
            const err = json.detail || (Array.isArray(json.detail) ? json.detail.join(', ') : json.message || res.statusText);
            if (typeof addMessageToUI === 'function') addMessageToUI('assistant', `❌ 진행 실패: ${err}`, false, null, null, false, null, null);
        }
    } catch (e) {
        console.error('[Evolution] Proceed from plan failed:', e);
        if (typeof addMessageToUI === 'function') addMessageToUI('assistant', '진행 중 오류가 발생했습니다.', false, null, null, false, null, null);
    } finally {
        if (btnEl) {
            btnEl.disabled = false;
            btnEl.innerHTML = '✅ 진행 승인';
        }
    }
}

/**
 * apply-from-proposal API 호출 (승인 버튼 클릭 시)
 */
async function applyEvolutionProposal(proposalId) {
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        return;
    }
    const baseUrl = (typeof API_BASE !== 'undefined') ? API_BASE : window.location.origin;
    const token = State.getAuthToken() || localStorage.getItem('auth_token');
    try {
        const res = await fetch(`${baseUrl}/evolution/apply-from-proposal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) },
            body: JSON.stringify({ proposal_id: proposalId })
        });
        const json = await res.json().catch(() => ({}));
        if (res.ok && json.success) {
            const card = document.getElementById(`evolution-report-${proposalId}`);
            if (card) {
                const wrap = card.querySelector('.evolution-approve-btn')?.closest('.flex.gap-2');
                if (wrap) {
                    wrap.innerHTML = `<span class="text-green-400 flex items-center gap-2"><i class="fas fa-check-circle"></i> 적용 완료</span>`;
                }
            }
            if (typeof addMessageToUI === 'function') {
                addMessageToUI('assistant', `✅ 제안서 ${proposalId.substring(0, 8)} 적용이 완료되었습니다.`, false, null, null, false, null, null);
            }
        } else {
            alert('적용 실패: ' + (json.detail || (Array.isArray(json.detail) ? json.detail.join(', ') : json.message || res.statusText)));
        }
    } catch (e) {
        console.error('[Evolution] Apply failed:', e);
        alert('적용 중 오류가 발생했습니다.');
    }
}
