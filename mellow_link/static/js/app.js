// =========================
// Application Bootstrap
// =========================

/**
 * 모드 토글
 */
function toggleMode() {
    const btn = document.getElementById('modeToggle');
    const icon = document.getElementById('modeIcon');
    const text = document.getElementById('modeText');

    if (State.getCurrentMode() === "auto") {
        State.setCurrentMode("fast");
        icon.className = "fas fa-bolt"; text.textContent = "Fast";
        btn.style.background = "linear-gradient(135deg, #f59e0b 0%, #f97316 100%)";
        btn.style.borderColor = "#f97316";
    } else if (State.getCurrentMode() === "fast") {
        State.setCurrentMode("thinking");
        icon.className = "fas fa-brain"; text.textContent = "Thinking";
        btn.style.background = "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)";
        btn.style.borderColor = "#8b5cf6";
    } else if (State.getCurrentMode() === "thinking") {
        if (State.getIsGuestMode()) {
            State.setCurrentMode("auto");
            icon.className = "fas fa-robot"; text.textContent = "Auto";
            btn.style.background = "linear-gradient(135deg, #10b981 0%, #059669 100%)";
            btn.style.borderColor = "#059669";
            const st = document.getElementById('statusText');
            st.textContent = "⚠️ Research mode requires login";
            st.classList.add('text-yellow-400');
            setTimeout(() => { st.textContent = 'Ready'; st.classList.remove('text-yellow-400'); }, 2000);
        } else {
            State.setCurrentMode("research");
            icon.className = "fas fa-microscope"; text.textContent = "Research";
            btn.style.background = "linear-gradient(135deg, #3b82f6 0%, #2563eb 100%)";
            btn.style.borderColor = "#2563eb";
        }
    } else {
        State.setCurrentMode("auto");
        icon.className = "fas fa-robot"; text.textContent = "Auto";
        btn.style.background = "linear-gradient(135deg, #10b981 0%, #059669 100%)";
        btn.style.borderColor = "#059669";
    }
}

/**
 * 파일 업로드 핸들러 (최종 수정본: 세션 ID 강제 발급)
 */
async function handleFileUpload(input) {
    const f = input.files[0]; 
    if (!f) return;

    const fd = new FormData(); 
    fd.append('file', f);

    // [UI] 상태 요소 확보
    const preview = document.getElementById('uploadPreview');
    const spinner = document.getElementById('uploadSpinner');
    const successIcon = document.getElementById('uploadSuccessIcon');
    const statusText = document.getElementById('uploadStatusText');

    // [State: Uploading]
    if (preview) preview.classList.remove('hidden');
    if (spinner) spinner.classList.remove('hidden');
    if (successIcon) successIcon.classList.add('hidden');
    if (statusText) statusText.textContent = '⏳ Uploading...'; 

    // [Logic] URL 설정
    // 혹시 State.getApiBase()가 없을 경우를 대비해 기본값 처리
    const baseUrl = State.getApiBase() || '';
    const url = `${baseUrl}/chat/upload-temp`;

    // -----------------------------------------------------------
    // 🔑 [Critical Fix] 세션 ID 없으면 즉석 발급 (Deadlock 해결)
    // -----------------------------------------------------------
    let activeSessionId = null;
    
    // 1. 현재 폴더 세션 확인
    if (State.getCurrentSessionId()) {
        activeSessionId = State.getCurrentSessionId();
    } else if (State.getTempSessionId()) {
        activeSessionId = State.getTempSessionId();
    }
    if (!activeSessionId) {
        activeSessionId = 'temp_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9);
        State.setTempSessionId(activeSessionId);
        console.log("[Upload] 임시 세션 ID 신규 발급:", activeSessionId);
    }

    // 이제 무조건 ID가 있으니 안심하고 첨부
    fd.append('session_id', activeSessionId);
    // -----------------------------------------------------------

    try {
        console.log(`[Upload] Sending to ${url} with session ${activeSessionId}`);
        
        const res = await fetch(url, { method: 'POST', body: fd });

        if (res.ok) { 
            const d = await res.json(); 
            console.log("[Upload] Success:", d);
            // ----------------------------------------------------------------
            // ✅ [FIX] 영수증 동기화: "방금 업로드한 세션이 곧 현재 세션이다"
            // ----------------------------------------------------------------
            if (typeof TEMP_SESSION_ID !== 'undefined') {
                // 서버가 확정해준 ID(d.session_id)가 있으면 쓰고, 없으면 우리가 보낸 거(activeSessionId) 씀
                const usedSessionId = d.session_id || activeSessionId;
                
                TEMP_SESSION_ID = usedSessionId;       // 내부 변수 갱신
                window.TEMP_SESSION_ID = usedSessionId; // 전역 변수(Window) 갱신 (chat.js가 볼 수 있게)
                
                console.log(`[Upload] Temp Session synced to: ${usedSessionId}`);
            }
            // ----------------------------------------------------------------

            // [State: Done]
            if (spinner) spinner.classList.add('hidden');
            if (successIcon) successIcon.classList.remove('hidden');
            if (statusText) statusText.textContent = '✅ Done!'; 
            
        } else {
            // 에러 내용을 확인하기 위해 텍스트로 읽어봄
            const errText = await res.text();
            console.error(`[Upload Error] Status: ${res.status}, Msg: ${errText}`);
            throw new Error(`Server Error: ${res.status}`);
        }
    } catch (e) {
        // [State: Failed]
        console.error(e);
        if (spinner) spinner.classList.add('hidden');
        if (successIcon) successIcon.classList.add('hidden');
        if (statusText) statusText.textContent = '❌ Failed'; 
    }
}

/**
 * 리포트 모달
 */
function showReportModal() {
    document.getElementById('reportModal').style.display = 'flex';
    // 입력 필드 초기화
    document.getElementById('reportCategory').value = 'bug';
    document.getElementById('reportSummary').value = '';
    document.getElementById('reportDetails').value = '';
}


async function submitReport() {
    const category = document.getElementById('reportCategory').value;
    const summary = document.getElementById('reportSummary').value.trim();
    const details = document.getElementById('reportDetails').value.trim();

    // 입력 검증
    if (!summary) {
        alert('Please enter a summary');
        return;
    }

    if (!details) {
        alert('Please enter details');
        return;
    }

    try {
        const response = await fetch(`${State.getApiBase()}/chat/report`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(State.getAuthToken() ? {'Authorization': `Bearer ${State.getAuthToken()}`} : {})
            },
            body: JSON.stringify({
                category: category,
                summary: summary,
                details: details,
                session_id: State.getCurrentSessionId(),
                message_id: null  // 필요시 마지막 메시지 ID 추가 가능
            })
        });

        if (response.ok) {
            const result = await response.json();
            alert('✅ Report submitted successfully! Thank you for your feedback.');
            closeModal('reportModal');
        } else {
            const error = await response.json();
            alert(`❌ Failed to submit report: ${error.detail || 'Unknown error'}`);
        }
    } catch (error) {
        console.error('Report submission error:', error);
        alert('❌ Network error. Please try again.');
    }
}

// =========================
// Mellow-Link Functions
// =========================

/**
 * Toggle Mellow-Link section expanded/collapsed
 */
function toggleMellowLink() {
    const content = document.getElementById('mellowLinkContent');
    const icon = document.getElementById('mellowLinkIcon');

    MELLOW_LINK_EXPANDED = !MELLOW_LINK_EXPANDED;

    if (MELLOW_LINK_EXPANDED) {
        content.style.display = 'block';
        icon.style.transform = 'rotate(0deg)';
    } else {
        content.style.display = 'none';
        icon.style.transform = 'rotate(-90deg)';
    }
}

/**
 * Refresh avatar status from server
 */
async function refreshAvatarStatus() {
    try {
        const res = await fetch(`${State.getApiBase()}/avatar/status`);
        if (res.ok) {
            const data = await res.json();
            updateAvatarStatusUI(data);
        }
    } catch (e) {
        console.error('[MellowLink] Failed to refresh avatar status:', e);
    }
}

/**
 * Update avatar status UI elements
 */
function updateAvatarStatusUI(data) {
    const dot = document.getElementById('avatarStatusDot');
    const text = document.getElementById('avatarStatusText');
    const launchBtn = document.getElementById('launchAvatarBtn');

    if (!dot || !text) return;

    const isConnected = data?.avatar_service?.port_active || data?.relay?.connected;
    const status = data?.avatar_service?.status || 'not_running';

    AVATAR_STATUS = {
        connected: isConnected,
        port_active: data?.avatar_service?.port_active || false,
        relay_connected: data?.relay?.connected || false,
        last_check: new Date()
    };

    // Update status dot color
    if (isConnected) {
        dot.className = 'w-2 h-2 rounded-full bg-green-500';
        dot.title = 'Avatar connected';
        text.textContent = 'Connected';
        text.className = 'text-green-400';
        if (launchBtn) launchBtn.style.display = 'none';
    } else if (status === 'starting') {
        dot.className = 'w-2 h-2 rounded-full bg-yellow-500 animate-pulse';
        dot.title = 'Avatar starting';
        text.textContent = 'Starting...';
        text.className = 'text-yellow-400';
    } else {
        dot.className = 'w-2 h-2 rounded-full bg-gray-500';
        dot.title = 'Avatar disconnected';
        text.textContent = 'Offline';
        text.className = 'text-gray-500';
        if (launchBtn) launchBtn.style.display = 'flex';
    }
}

/**
 * Launch avatar service (admin only)
 */
async function launchAvatar() {
    // 권한 체크 (isAdmin/IS_ADMIN 동기화)
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        else showNotification('본 기능은 하우스 관리자(Admin) 전용 구역입니다.', 'warning');
        return;
    }

    if (!State.getAuthToken()) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }

    const btn = document.getElementById('launchAvatarBtn');
    const originalContent = btn ? btn.innerHTML : '';
    let isLaunching = true;

    // 버튼 비활성화 및 로딩 상태 표시
    if (btn) {
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Launching...';
        btn.disabled = true;
        btn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    try {
        const response = await fetch(`${State.getApiBase()}/admin/launch_avatar`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${State.getAuthToken()}`
            }
        });

        const data = await response.json();

        if (response.ok && data.success) {
            // 성공 메시지
            showNotification(
                data.message || 'VTuber 아바타가 성공적으로 실행되었습니다.',
                'success',
                5000
            );

            // 상세 정보 로그
            console.log('[MellowLink] Avatar launched:', {
                pid: data.pid,
                server_ready: data.server_ready,
                electron_launched: data.electron_launched
            });

            // 상태 새로고침 (약간의 지연 후)
            setTimeout(async () => {
                await refreshAvatarStatus();
            }, 2000);
        } else {
            // 실패 메시지
            const errorMsg = data.detail || data.message || '아바타 실행에 실패했습니다.';
            
            if (response.status === 403) {
                showNotification('Admin 권한이 필요합니다.', 'error');
            } else if (response.status === 401) {
                showNotification('인증이 필요합니다. 다시 로그인해주세요.', 'error');
            } else {
                showNotification(errorMsg, 'error', 6000);
            }
        }
    } catch (error) {
        console.error('[MellowLink] Failed to launch avatar:', error);
        showNotification(
            '네트워크 오류가 발생했습니다. 다시 시도해주세요.',
            'error'
        );
    } finally {
        // 버튼 상태 복원
        isLaunching = false;
        if (btn) {
            btn.innerHTML = originalContent;
            btn.disabled = false;
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    }
}

/**
 * 자가발전 요청 (삼권분립 파이프라인) - Admin 전용
 * Mellow_Link_Spec.md Step 5
 */
async function launchEvolutionCycle() {
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        else showNotification('본 기능은 하우스 관리자(Admin) 전용 구역입니다.', 'warning');
        return;
    }
    if (!State.getAuthToken()) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }
    const userRequest = prompt('수정 요청을 입력하세요 (무엇을 고칠지):');
    if (!userRequest || !userRequest.trim()) return;

    const btn = document.getElementById('evolutionCycleBtn');
    const originalContent = btn ? btn.innerHTML : '';
    if (btn) {
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> 진행 중...';
        btn.disabled = true;
        btn.classList.add('opacity-50', 'cursor-not-allowed');
    }

    try {
        const res = await fetch(`${State.getApiBase()}/evolution/cycle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` },
            body: JSON.stringify({ user_request: userRequest.trim() })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.id) {
            const isSkipDup = data.error && String(data.error).includes('SKIP_DUPLICATE');
            const isLowRoi = data.error && String(data.error).includes('LOW_ROI_SUGGESTION');
            const isPlanPending = data.plan_pending === true;
            if (isSkipDup) {
                showNotification(data.message || '이미 유사한 제안이 대기 중이어서 다른 작업을 준비합니다.', 'info', 5000);
            } else if (isLowRoi) {
                showNotification(data.message || '이 판은 판돈 대비 수익률이 낮습니다. 방식을 바꾸거나 보류하는 게 어떨까요?', 'info', 6000);
            } else if (isPlanPending) {
                showNotification(data.message || 'Proposed Plan 보고됨. 진행 승인 후 코드 생성이 진행됩니다.', 'info', 6000);
                if (typeof renderEvolutionPlanPending === 'function') {
                    renderEvolutionPlanPending(data);
                } else if (typeof renderEvolutionReport === 'function') {
                    renderEvolutionReport(data);
                }
            } else {
                if (typeof renderEvolutionReport === 'function') {
                    renderEvolutionReport(data);
                }
                showNotification('결재 보고서가 생성되었습니다.', 'success');
            }
        } else {
            const err = data.detail || data.message || (Array.isArray(data.detail) ? data.detail.join(', ') : '요청 실패');
            showNotification('자가발전 요청 실패: ' + err, 'error', 6000);
        }
    } catch (e) {
        console.error('[Evolution] Cycle failed:', e);
        showNotification('네트워크 오류가 발생했습니다.', 'error');
    } finally {
        if (btn) {
            btn.innerHTML = originalContent;
            btn.disabled = false;
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
        }
    }
}

/**
 * 자율 작업 보고서 모달 표시 (Admin 전용)
 */
async function showAutonomousReportModal() {
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        else showNotification('본 기능은 하우스 관리자(Admin) 전용 구역입니다.', 'warning');
        return;
    }
    if (!State.getAuthToken()) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }
    document.getElementById('autonomousReportModal').style.display = 'flex';
    const content = document.getElementById('autonomousReportContent');
    content.innerHTML = '<p class="text-gray-500">로딩 중...</p>';
    try {
        const [autoRes, evoRes] = await Promise.all([
            fetch(`${State.getApiBase()}/autonomous/report`, { headers: { 'Authorization': `Bearer ${State.getAuthToken()}` } }),
            fetch(`${State.getApiBase()}/evolution/waiting-for-approval`, { headers: { 'Authorization': `Bearer ${State.getAuthToken()}` } })
        ]);
        const data = await autoRes.json().catch(() => ({}));
        const evoData = await evoRes.json().catch(() => ({}));
        if (!autoRes.ok) {
            content.innerHTML = `<p class="text-red-400">조회 실패: ${data.detail || autoRes.statusText}</p>`;
            return;
        }
        let html = '';
        const waiting = data.waiting_for_approval || [];
        const evoWaiting = evoData.waiting || [];
        const recent = data.recent || [];

        // 일괄 정리 버튼 (대기 항목이 하나라도 있을 때)
        const totalWaiting = waiting.length + evoWaiting.length;
        if (totalWaiting > 0) {
            html += `<div class="flex justify-end mb-3 gap-2">
                <button onclick="rejectAllPending()" class="px-3 py-1.5 bg-gray-700 hover:bg-red-700 rounded-lg text-xs text-gray-300 hover:text-white transition-colors border border-gray-600 hover:border-red-500">
                    🗑️ 대기 항목 전체 정리 (${totalWaiting}건)
                </button>
            </div>`;
        }

        if (evoWaiting.length > 0) {
            html += '<h3 class="font-semibold text-amber-300 mb-2">🏛️ 자가발전(Evolution) 승인 대기</h3>';
            evoWaiting.forEach(r => {
                const req = (r.user_request || '').substring(0, 150);
                html += `<div class="evolution-report-card border border-amber-500/30 rounded-lg p-3 mb-3 bg-dark-hover/50">
                    <div class="flex flex-wrap justify-between items-start gap-2 mb-2">
                        <span class="font-mono text-xs text-gray-500">${escapeHtml(r.id?.substring(0,8) || '')}</span>
                        <div class="flex gap-2 flex-shrink-0">
                            <button onclick="approveEvolutionProposal('${r.id}')" class="evolution-approve-btn px-3 py-2 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-medium">승인</button>
                            <button onclick="rejectEvolutionProposal('${r.id}')" class="evolution-reject-btn px-3 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium">거부</button>
                        </div>
                    </div>
                    <div class="text-xs"><strong>대상:</strong> ${escapeHtml(r.verdict_target_file || '')}</div>
                    <div class="text-xs mt-1"><strong>요청:</strong> ${escapeHtml(req)}${req.length >= 150 ? '...' : ''}</div>
                </div>`;
            });
        }
        html += '<h3 class="font-semibold text-indigo-300 mb-2 mt-4">⏳ 자율 작업 승인 대기</h3>';
        if (waiting.length === 0 && evoWaiting.length === 0) {
            html += '<p class="text-gray-500 mb-4">승인 대기 항목이 없습니다.</p>';
        } else if (waiting.length === 0) {
            html += '<p class="text-gray-500 mb-4">자율 작업 승인 대기 항목 없음.</p>';
        } else {
            waiting.forEach(r => {
                html += `<div class="autonomous-report-card border border-indigo-500/30 rounded-lg p-3 mb-3 bg-dark-hover/50">
                    <div class="flex flex-wrap justify-between items-start gap-2 mb-2">
                        <span class="font-mono text-xs text-gray-500">${escapeHtml(r.id?.substring(0,8) || '')}</span>
                        <div class="flex gap-2 flex-shrink-0">
                            <button onclick="approveAutonomousWork('${r.id}')" class="autonomous-approve-btn px-3 py-2 bg-green-600 hover:bg-green-700 rounded-lg text-sm font-medium">승인</button>
                            <button onclick="rejectAutonomousWork('${r.id}')" class="autonomous-reject-btn px-3 py-2 bg-red-600 hover:bg-red-700 rounded-lg text-sm font-medium">거부</button>
                        </div>
                    </div>
                    <div class="text-xs"><strong>도구:</strong> ${escapeHtml((r.tools_created || '').substring(0,200))}</div>
                    <div class="text-xs mt-1"><strong>정보:</strong> ${escapeHtml((r.info_collected || '').substring(0,200))}</div>
                    <div class="text-xs mt-1 text-gray-400"><strong>윤리 검토:</strong> ${escapeHtml((r.ethics_review || '').substring(0,300))}</div>
                </div>`;
            });
        }
        if (recent.length > 0) {
            html += '<h3 class="font-semibold text-gray-400 mt-4 mb-2">📋 최근 작업</h3><div class="space-y-3">';
            const statusLabels = { APPROVED: '✅ 완료', COMPLETED: '✅ 완료', REJECTED: '❌ 거부됨', QUARANTINED: '⚠️ 격리됨', WAITING_FOR_APPROVAL: '⏳ 승인 대기', ETHICS_FAIL: '❌ 윤리 검토 실패' };
            recent.forEach(r => {
                const topic = [r.tools_created, r.info_collected].filter(Boolean).join(' / ').slice(0, 150) || '(주제 없음)';
                const statusClass = r.status === 'QUARANTINED' ? 'text-amber-400' : r.status === 'WAITING_FOR_APPROVAL' ? 'text-indigo-400' : (r.status === 'APPROVED' || r.status === 'COMPLETED') ? 'text-green-400' : r.status === 'REJECTED' ? 'text-red-400' : 'text-gray-400';
                const statusText = statusLabels[r.status] || r.status;
                html += `<div class="autonomous-report-card border border-gray-600/50 rounded-lg p-3 bg-dark-hover/30">
                    <div class="flex justify-between items-center mb-1">
                        <span class="font-mono text-xs text-gray-500">${escapeHtml(r.id?.substring(0,8) || '')}</span>
                        <span class="text-xs font-medium ${statusClass}">${escapeHtml(statusText)}</span>
                    </div>
                    <div class="text-xs text-gray-400 mb-1"><strong>주제:</strong> ${escapeHtml(topic)}${topic.length >= 150 ? '...' : ''}</div>`;
                if (r.ethics_review && (r.status === 'QUARANTINED' || r.status === 'WAITING_FOR_APPROVAL')) {
                    html += `<div class="text-xs text-amber-300/90 mt-1"><strong>거부/검토 사유:</strong> ${escapeHtml((r.ethics_review || '').substring(0, 400))}${(r.ethics_review || '').length > 400 ? '...' : ''}</div>`;
                }
                if (r.output && (r.status === 'COMPLETED' || r.status === 'APPROVED')) {
                    html += `<div class="text-xs text-green-300/90 mt-1"><strong>실행 결과:</strong> ${escapeHtml((r.output || '').substring(0, 600))}${(r.output || '').length > 600 ? '...' : ''}</div>`;
                }
                html += `<div class="text-xs text-gray-500 mt-1">${escapeHtml(r.created_at || '')}</div></div>`;
            });
            html += '</div>';
        }
        content.innerHTML = html || '<p class="text-gray-500">데이터 없음</p>';
    } catch (e) {
        content.innerHTML = `<p class="text-red-400">오류: ${escapeHtml(String(e))}</p>`;
    }
}

async function approveAutonomousWork(recordId) {
    if (!State.getAuthToken()) return;
    try {
        const res = await fetch(`${State.getApiBase()}/autonomous/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` },
            body: JSON.stringify({ record_id: recordId })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showNotification('승인 완료', 'success');
            if (typeof closeModal === 'function') closeModal('autonomousReportModal');
            else showAutonomousReportModal();
        } else {
            showNotification(data.detail || '승인 실패', 'error');
        }
    } catch (e) {
        showNotification('오류 발생', 'error');
    }
}

async function approveEvolutionProposal(proposalId) {
    if (!State.getAuthToken()) return;
    try {
        const res = await fetch(`${State.getApiBase()}/evolution/apply-from-proposal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` },
            body: JSON.stringify({ proposal_id: proposalId })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showNotification('자가발전 적용 완료', 'success');
            if (typeof closeModal === 'function') closeModal('autonomousReportModal');
            else showAutonomousReportModal();
        } else {
            showNotification(data.detail || '적용 실패', 'error');
        }
    } catch (e) {
        showNotification('오류 발생', 'error');
    }
}

async function rejectEvolutionProposal(proposalId) {
    if (!State.getAuthToken()) return;
    try {
        const res = await fetch(`${State.getApiBase()}/evolution/reject-proposal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` },
            body: JSON.stringify({ proposal_id: proposalId })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showNotification('거부 완료', 'success');
            if (typeof closeModal === 'function') closeModal('autonomousReportModal');
            else showAutonomousReportModal();
        } else {
            showNotification(data.detail || '거부 실패', 'error');
        }
    } catch (e) {
        showNotification('오류 발생', 'error');
    }
}

async function rejectAutonomousWork(recordId) {
    if (!State.getAuthToken()) return;
    try {
        const res = await fetch(`${State.getApiBase()}/autonomous/reject`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` },
            body: JSON.stringify({ record_id: recordId })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showNotification('거부 완료', 'success');
            if (typeof closeModal === 'function') closeModal('autonomousReportModal');
            else showAutonomousReportModal();
        } else {
            showNotification(data.detail || '거부 실패', 'error');
        }
    } catch (e) {
        showNotification('오류 발생', 'error');
    }
}

/**
 * 대기 항목 전체 일괄 정리 (자율 작업 + Evolution 모두 거부)
 */
async function rejectAllPending() {
    if (!State.getAuthToken()) return;
    if (!confirm('⚠️ 대기 중인 자율 작업 + Evolution 제안서를 전부 거부합니다.\n(이미 완료된 작업에는 영향 없음)\n\n진행하시겠습니까?')) return;

    let totalRejected = 0;
    try {
        // 자율 작업 일괄 거부
        const autoRes = await fetch(`${State.getApiBase()}/autonomous/reject-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` }
        });
        const autoData = await autoRes.json().catch(() => ({}));
        if (autoRes.ok) totalRejected += (autoData.rejected_count || 0);

        // Evolution 제안서 일괄 거부
        const evoRes = await fetch(`${State.getApiBase()}/evolution/reject-all`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${State.getAuthToken()}` }
        });
        const evoData = await evoRes.json().catch(() => ({}));
        if (evoRes.ok) totalRejected += (evoData.rejected_count || 0);

        showNotification(`✅ ${totalRejected}건 일괄 정리 완료`, 'success');
        showAutonomousReportModal(); // 새로고침
    } catch (e) {
        showNotification('일괄 정리 중 오류 발생', 'error');
    }
}

/**
 * 자율 틱 1회 즉시 실행 (테스트/검증용, Admin 전용)
 */
async function runAutonomousTickNow() {
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        else showNotification('본 기능은 Admin 전용입니다.', 'warning');
        return;
    }
    if (!State.getAuthToken()) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }
    showNotification('틱 실행 중... (30초~1분 소요)', 'info');
    try {
        const res = await fetch(`${State.getApiBase()}/autonomous/run-tick`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${State.getAuthToken()}` }
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showNotification(`완료: ${data.message || data.status}`, 'success');
            showAutonomousReportModal();
        } else {
            showNotification(data.detail || '실행 실패', 'error');
        }
    } catch (e) {
        showNotification('오류: ' + (e.message || '네트워크 오류'), 'error');
    }
}

/**
 * Select Secretary folder (admin only)
 */
function selectSecretaryFolder() {
    if (!SECRETARY_FOLDER_ID) {
        console.warn('[MellowLink] Secretary folder ID not set');
        return;
    }
    selectFolder(SECRETARY_FOLDER_ID);
}

/**
 * Initialize Mellow-Link UI based on user role
 */
async function initMellowLink() {
    const mellowLinkSection = document.getElementById('mellowLinkSection');
    
    // 게스트 모드이거나 토큰이 없으면 숨김
    if (!State.getAuthToken() || State.getIsGuestMode()) {
        if (mellowLinkSection) {
            mellowLinkSection.style.display = 'none';
        }
        return;
    }

    try {
        const res = await fetch(`${State.getApiBase()}/mellow-link/init`, {
            headers: { 'Authorization': `Bearer ${State.getAuthToken()}` }
        });

        if (res.ok) {
            const data = await res.json();

            if (data.success) {
                // Update admin status (state.js isAdmin와 동기화)
                const adminVal = data.is_admin || false;
                State.setIsAdmin(adminVal);

                // Admin만 Mellow-Link 섹션 표시
                if (mellowLinkSection) {
                    if (adminVal) {
                        mellowLinkSection.style.display = 'block';
                    } else {
                        mellowLinkSection.style.display = 'none';
                    }
                }

                // Show Secretary folder for admin
                if (adminVal && data.folders) {
                    const secretaryFolder = data.folders.find(f => f.name.includes('Secretary'));
                    if (secretaryFolder) {
                        SECRETARY_FOLDER_ID = secretaryFolder.id;
                        const secretaryFolderEl = document.getElementById('secretaryFolder');
                        if (secretaryFolderEl) {
                            secretaryFolderEl.classList.remove('hidden');
                        }
                    }
                } else {
                    // Admin이 아니면 Secretary 폴더 숨김
                    const secretaryFolderEl = document.getElementById('secretaryFolder');
                    if (secretaryFolderEl) {
                        secretaryFolderEl.classList.add('hidden');
                    }
                }

                // Update avatar status
                if (data.avatar_status) {
                    updateAvatarStatusUI(data.avatar_status);
                }

                // [Admin Auto-Refresh] Electron이 백그라운드로 실행되므로 몇 초 후 상태 재확인
                if (adminVal) {
                    console.log('[MellowLink] Admin detected - scheduling avatar status refresh...');
                    // 3초 후 첫 번째 체크
                    setTimeout(() => {
                        console.log('[MellowLink] Auto-refreshing avatar status (3s)...');
                        refreshAvatarStatus();
                    }, 3000);
                    // 6초 후 두 번째 체크 (Electron 실행 완료 대기)
                    setTimeout(() => {
                        console.log('[MellowLink] Auto-refreshing avatar status (6s)...');
                        refreshAvatarStatus();
                    }, 6000);
                }

                console.log('[MellowLink] Initialized:', { is_admin: adminVal, secretary_id: SECRETARY_FOLDER_ID });
            } else {
                // 실패 시 섹션 숨김
                if (mellowLinkSection) {
                    mellowLinkSection.style.display = 'none';
                }
            }
        } else {
            // API 실패 시 섹션 숨김
            if (mellowLinkSection) {
                mellowLinkSection.style.display = 'none';
            }
        }
    } catch (e) {
        console.error('[MellowLink] Init failed:', e);
        // 에러 시 섹션 숨김
        if (mellowLinkSection) {
            mellowLinkSection.style.display = 'none';
        }
    }
}

/**
 * 앱 초기화
 */
window.onload = async () => {
    console.log('🚀 App loading...');

    // ============================================
    // [STEP 1] Access Gate Check (MUST PASS FIRST)
    // ============================================
    const hasAccess = await checkAccessGate();
    if (!hasAccess) {
        console.log('🔒 Access gate active - waiting for authentication');
        // Don't initialize rest of app until authenticated
        // The page will reload after successful login
        return;
    }

    // ============================================
    // [STEP 2] Normal App Initialization
    // ============================================
    document.getElementById('tempSlider').addEventListener('input', updateTempDisplay);

    // 인증 상태 확인 및 UI 업데이트
    if (State.getAuthToken()) {
        await checkAuth();
    } else {
        switchToGuestUI();
    }

    // 온도 슬라이더 초기화
    updateTempDisplay();

    // ✅ [VRAM] VRAM 모니터링 시작
    startVRAMPolling();

    // ✅ [MELLOW-LINK] Initialize Mellow-Link UI
    await initMellowLink();

    // ✅ [SESSION PERSISTENCE] URL 파라미터 자동 로드 (새로고침 유지)
    const urlParams = new URLSearchParams(window.location.search);
    const sessionId = urlParams.get('session_id');

    if (sessionId) {
        console.log(`🔗 [URL] Found session_id=${sessionId}, auto-loading...`);

        // 세션 자동 로드 (약간의 지연으로 UI 초기화 완료 대기)
        setTimeout(async () => {
            try {
                await loadSession(parseInt(sessionId));
                console.log(`✅ [URL] Auto-loaded session ${sessionId}`);
            } catch (e) {
                console.error(`❌ [URL] Failed to auto-load session ${sessionId}:`, e);
                // 실패 시 URL 파라미터 제거
                history.replaceState(null, '', window.location.pathname);
            }
        }, 300);  // 300ms 지연 (폴더/세션 목록 로드 대기)
    }

    // 시스템 플로우 관전: 사이드바 요약 토글 + 상세 보기는 별도 창
    const btnMonitorFlow = document.getElementById('btn-monitor-flow');
    if (btnMonitorFlow) {
        btnMonitorFlow.addEventListener('click', toggleMonitorFlow);
    }

    console.log('✅ App ready');
};

/**
 * 시스템 플로우 관전 - 사이드바 슬림 요약 토글 (Admin 전용)
 */
function toggleMonitorFlow() {
    const adminOk = State.getIsAdmin();
    if (!adminOk) {
        if (typeof showAdminOnlyWarning === 'function') showAdminOnlyWarning();
        else showNotification('본 기능은 하우스 관리자(Admin) 전용 구역입니다.', 'warning');
        return;
    }
    if (!State.getAuthToken() && !localStorage.getItem('auth_token')) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }
    const area = document.getElementById('flow-display-area');
    const list = document.getElementById('flow-list');
    if (!area || !list) return;

    if (area.style.display === 'none' || !area.style.display) {
        area.style.display = 'block';
        list.innerHTML = '<p class="text-gray-500 text-[10px] py-2 px-2">로딩 중...</p>';
        loadMonitorFlowBrief();
    } else {
        area.style.display = 'none';
    }
}

async function loadMonitorFlowBrief() {
    const list = document.getElementById('flow-list');
    if (!list) return;
    const token = State.getAuthToken() || localStorage.getItem('auth_token');
    if (!token) {
        list.innerHTML = '<p class="text-amber-400 text-[10px] py-2 px-2">관리자 권한을 확인해주세요.</p>';
        return;
    }
    try {
        const res = await fetch(`${State.getApiBase()}/monitor/flow?minutes=30&limit=30`, {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) {
            list.innerHTML = '<p class="text-amber-400 text-[10px] py-2 px-2">관리자 권한을 확인하거나, 첫 이벤트를 생성해주세요.</p>';
            return;
        }
        const data = await res.json().catch(() => ({}));
        const events = data.events || [];
        if (events.length === 0) {
            list.innerHTML = '<p class="text-gray-500 text-[10px] py-2 px-2">이벤트 없음</p>';
            return;
        }
        const icons = { CHAT: '🤖', EVOLUTION: '🏛️', INSIGHT: '💡', GOAL: '🎯' };
        const esc = typeof escapeHtml === 'function' ? escapeHtml : (s) => (s || '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] || c);
        list.innerHTML = events.map((ev, i) => {
            const t = ev.type || 'CHAT';
            const icon = t === 'EVOLUTION' && ev.is_approved === false ? '🚫' : (icons[t] || '•');
            let title = '';
            if (t === 'CHAT') title = (ev.task_intent || '').slice(0, 35);
            else if (t === 'EVOLUTION') title = (ev.target_file || ev.reason || '').slice(0, 35);
            else if (t === 'INSIGHT') title = (ev.finding || '').slice(0, 35);
            else if (t === 'GOAL') title = (ev.title || '').slice(0, 35);
            else title = (ev.id || '').slice(0, 12);
            const timeStr = (ev.time || '').slice(11, 19) || (ev.time || '').slice(0, 8) || '';
            const crit = t === 'EVOLUTION' && ev.is_approved === false ? ' flow-critical' : '';
            const eid = (ev.id || '').replace(/"/g, '&quot;');
            return `<div class="flow-brief-card${crit}">
                <span class="flow-brief-icon">${icon}</span>
                <div class="flow-brief-body">
                    <div class="flow-brief-time">${timeStr}</div>
                    <div class="flow-brief-title">${esc(title)}${(title.length >= 35 ? '...' : '')}</div>
                </div>
                <button class="flow-brief-detail-btn" data-event-id="${eid}" onclick="openFlowDetail(this.dataset.eventId)">상세</button>
            </div>`;
        }).join('');
    } catch (e) {
        console.error('[MonitorFlow]', e);
        list.innerHTML = '<p class="text-amber-400 text-[10px] py-2 px-2">관리자 권한을 확인하거나, 첫 이벤트를 생성해주세요.</p>';
    }
}

function openFlowDetail(eventId) {
    const token = State.getAuthToken() || localStorage.getItem('auth_token');
    if (!token) {
        showNotification('로그인이 필요합니다.', 'error');
        return;
    }
    const url = `/monitor/flow/detail/${encodeURIComponent(eventId)}?access_token=${encodeURIComponent(token)}`;
    window.open(url, '_blank', 'width=1000,height=800');
}
