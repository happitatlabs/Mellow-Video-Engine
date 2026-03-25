// =========================
// Folders & Sessions Module
// =========================

async function loadFolders() {
    if(!State.getAuthToken()) return;
    const res=await fetch(`${State.getApiBase()}/folders`,{headers:{'Authorization':`Bearer ${State.getAuthToken()}`}});
    if(res.ok) { State.setFolders(await res.json()); renderFolders(); }
}

function renderFolders() {
    const list=document.getElementById('foldersList'); list.innerHTML='';
    State.getFolders().forEach(f=>{
        const div=document.createElement('div'); div.className='bg-dark-hover rounded-lg overflow-hidden';
        div.innerHTML=`<div class="flex items-center justify-between p-3 hover:bg-dark-border"><div class="flex items-center gap-2 flex-1 cursor-pointer" onclick="toggleFolderAccordion(${f.id})"><span class="text-xl">${f.icon}</span><div><div class="font-medium text-sm">${escapeHtml(f.name)}</div><div class="text-xs text-gray-500">${f.session_count} sessions</div></div></div><div class="flex items-center gap-1"><button onclick="event.stopPropagation(); showFolderSettings(${f.id})" class="p-2 text-gray-400 hover:text-purple-400"><i class="fas fa-cog text-sm"></i></button><button onclick="event.stopPropagation(); toggleFolderAccordion(${f.id})" class="p-2"><span id="folderIcon${f.id}">▼</span></button></div></div><div id="folderSessions${f.id}" class="accordion-content"><div class="p-2 space-y-1" id="sessionsList${f.id}"><button onclick="selectFolder(${f.id})" class="w-full text-left px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm">+ New Chat in ${escapeHtml(f.name)}</button></div></div>`;
        list.appendChild(div); loadFolderSessions(f.id);
    });
}

async function loadFolderSessions(fid) {
    const res=await fetch(`${State.getApiBase()}/folders/${fid}/sessions`,{headers:{'Authorization':`Bearer ${State.getAuthToken()}`}});
    if(res.ok) {
        const s=await res.json(); const c=document.getElementById(`sessionsList${fid}`);
        s.forEach(ss=>{
            const d=document.createElement('div'); d.className='group relative px-3 py-2 bg-dark-bg hover:bg-dark-border rounded text-sm cursor-pointer truncate flex items-center justify-between';
            // ✅ [FIX] folder_id 전달하여 CURRENT_FOLDER 보존
            d.innerHTML=`<div class="flex-1 truncate" onclick="loadSession(${ss.id}, ${fid})"><div class="font-medium truncate">${escapeHtml(ss.title)}</div></div><button onclick="event.stopPropagation(); deleteSession(${ss.id},${fid},this)" class="opacity-0 group-hover:opacity-100 p-1 hover:bg-red-600 rounded text-gray-400 hover:text-white"><i class="fas fa-trash text-xs"></i></button>`;
            c.appendChild(d);
        });
    }
}


function selectFolder(id) {
    // ✅ [SESSION PERSISTENCE] URL 파라미터 제거 (새 채팅 in 폴더)
    history.pushState(null, '', window.location.pathname);
    console.log(`🔗 [URL] Cleared session_id (new chat in folder ${id})`);

    State.setCurrentFolderId(id);
    State.setCurrentSessionId(null);
    State.setTempSessionId(null);
    var folder = State.getFolders().find(x=>x.id===id);
    State.setCurrentFolder(folder);
    document.getElementById('chatMessages').innerHTML='<div class="text-center text-gray-500 py-8"><p class="text-xl">New Chat in '+escapeHtml(folder.name)+'</p></div>';
    document.getElementById('sessionInfo').textContent=`Folder: ${folder.name}`;
    const ragStatusInfo = document.getElementById('ragStatus');
    if (ragStatusInfo) {
        if (folder.use_rag) {
            ragStatusInfo.classList.remove('hidden');
        } else {
            ragStatusInfo.classList.add('hidden');
        }
    }
}

 
function newChat() {
    history.pushState(null, '', window.location.pathname);
    console.log(`🔗 [URL] Cleared session_id (new chat)`);
    State.setCurrentSessionId(null);
    State.setCurrentFolderId(null);
    document.getElementById('chatMessages').innerHTML='<div class="text-center text-gray-500 py-8">New Chat</div>';
    document.getElementById('sessionInfo').textContent='';
}

async function loadUncategorizedSessions() {
    if(!State.getAuthToken()) return;
    const res=await fetch(`${State.getApiBase()}/chat/sessions/uncategorized`,{headers:{'Authorization':`Bearer ${State.getAuthToken()}`}});
    if(res.ok) {
        const s=await res.json(); const c=document.getElementById('uncategorizedList'); c.innerHTML='';
        if(s.length===0) c.innerHTML='<div class="text-xs text-gray-500 p-2">No sessions</div>';
        s.forEach(ss=>{
            const d=document.createElement('div'); d.className='group bg-dark-hover rounded p-2 cursor-pointer flex justify-between';
            d.innerHTML=`<div onclick="loadSession(${ss.id})" class="truncate flex-1">${escapeHtml(ss.title)}</div><button onclick="deleteSession(${ss.id},null,this)" class="opacity-0 group-hover:opacity-100 hover:text-red-400"><i class="fas fa-trash"></i></button>`;
            c.appendChild(d);
        });
    }
}


function toggleUncategorized() { 
    const l=document.getElementById('uncategorizedList'); 
    l.style.display=l.style.display==='none'?'block':'none'; 
}

async function createFolder() {
    const n=document.getElementById('folderName').value; const p=document.getElementById('folderSystemPrompt').value;
    const r=document.getElementById('folderUseRAG').checked; const c=document.getElementById('folderIsCreative').checked;
    const i=document.getElementById('selectedIcon').value;
    await fetch(`${State.getApiBase()}/folders`,{method:'POST',headers:{'Authorization':`Bearer ${State.getAuthToken()}`,'Content-Type':'application/json'},body:JSON.stringify({name:n,system_prompt:p,use_rag:r,icon:i,is_creative:c})});
    closeModal('createFolderModal'); loadFolders();
}

function showCreateFolderModal() { 
    if(State.getIsGuestMode()) return alert('Login required'); 
    document.getElementById('createFolderModal').style.display='flex'; 
}

function toggleFolderAccordion(id) { 
    const c=document.getElementById(`folderSessions${id}`); 
    c.classList.toggle('open'); 
}

async function deleteSession(sid, fid=null, btnElement=null) {
    if(confirm('Delete session?')) {
        const res = await fetch(`${State.getApiBase()}/chat/sessions/${sid}`,{method:'DELETE',headers:{'Authorization':`Bearer ${State.getAuthToken()}`}});

        if(res.ok) {
            // ✅ [FIX-3] Immediately remove DOM element (no afterimage)
            // Find the session element to remove
            let sessionEl = null;
            if(btnElement) {
                // If button element was passed, find parent session div
                sessionEl = btnElement.closest('.group');
            }
            if(!sessionEl) {
                // Fallback: find by session ID in onclick
                document.querySelectorAll('[onclick*="loadSession"]').forEach(el => {
                    if(el.onclick && el.onclick.toString().includes(`loadSession(${sid}`)) {
                        sessionEl = el.closest('.group');
                    }
                });
            }

            // Remove the element immediately
            if(sessionEl) {
                sessionEl.remove();
                console.log(`🗑️ [UI] Session ${sid} element removed immediately`);
            }

            // Update folder session count if applicable
            if(fid) {
                const folder = State.getFolders().find(f => f.id === fid);
                if(folder && folder.session_count > 0) {
                    folder.session_count--;
                    // Update the count display in folder header
                    const folderDiv = document.querySelector(`#folderSessions${fid}`)?.closest('.bg-dark-hover');
                    if(folderDiv) {
                        const countEl = folderDiv.querySelector('.text-xs.text-gray-500');
                        if(countEl) countEl.textContent = `${folder.session_count} sessions`;
                    }
                }
            }

            // ✅ [SESSION PERSISTENCE] 현재 세션 삭제 시 URL 파라미터도 제거
            if(State.getCurrentSessionId()===sid) {
                newChat();  // 이미 URL 파라미터 제거 포함됨
            }
        } else {
            console.error(`[UI] Failed to delete session ${sid}`);
            alert('Failed to delete session');
        }
    }
}

// Simplified versions of other folder functions
// Folder Settings & Docs (원본 유지)
async function showFolderSettings(fid) {
    State.setCurrentFolderSettingsId(fid); const f=State.getFolders().find(x=>x.id===fid); if(!f) return;
    document.getElementById('folderSettingsIcon').textContent=f.icon; document.getElementById('editFolderName').value=f.name;
    document.getElementById('editFolderPrompt').value=f.system_prompt; document.getElementById('editFolderIsCreative').checked=f.is_creative;
    loadFolderDocuments(fid); document.getElementById('folderSettingsModal').style.display='flex';
}
async function loadFolderDocuments(fid) {
    const c=document.getElementById('folderDocumentsList'); c.innerHTML='Loading...';
    const res=await fetch(`${State.getApiBase()}/folders/${fid}/documents`,{headers:{'Authorization':`Bearer ${State.getAuthToken()}`}});
    if(res.ok) {
        const docs=await res.json(); c.innerHTML=docs.length?docs.map(d=>`<div class="flex justify-between p-2 bg-dark-hover mb-1 rounded"><span>${escapeHtml(d.filename)}</span><button onclick="deleteFolderDocument(${fid},${d.id})" class="text-red-400"><i class="fas fa-trash"></i></button></div>`).join(''):'No docs';
    }
}
async function uploadToCurrentFolder(input) {
    const f=input.files[0]; var fid=State.getCurrentFolderSettingsId(); if(!f || !fid) return;
    const fd=new FormData(); fd.append('file',f);
    await fetch(`${State.getApiBase()}/folders/${fid}/upload`,{method:'POST',headers:{'Authorization':`Bearer ${State.getAuthToken()}`},body:fd});
    loadFolderDocuments(fid);
}
async function deleteFolderDocument(fid, did) {
    if(confirm('Delete?')) { await fetch(`${State.getApiBase()}/folders/${fid}/documents/${did}`,{method:'DELETE',headers:{'Authorization':`Bearer ${State.getAuthToken()}`}}); loadFolderDocuments(fid); }
}
async function saveFolderSettings() {
    const n=document.getElementById('editFolderName').value; const p=document.getElementById('editFolderPrompt').value; const c=document.getElementById('editFolderIsCreative').checked;
    var fid=State.getCurrentFolderSettingsId();
    await fetch(`${State.getApiBase()}/folders/${fid}`,{method:'PATCH',headers:{'Authorization':`Bearer ${State.getAuthToken()}`,'Content-Type':'application/json'},body:JSON.stringify({name:n,system_prompt:p,is_creative:c})});
    closeModal('folderSettingsModal'); loadFolders();
}
async function confirmDeleteFolder() {
    var fid=State.getCurrentFolderSettingsId();
    if(confirm('Delete folder?')) { await fetch(`${State.getApiBase()}/folders/${fid}`,{method:'DELETE',headers:{'Authorization':`Bearer ${State.getAuthToken()}`}}); closeModal('folderSettingsModal'); loadFolders(); }
}

function selectEmoji(el,v) { document.querySelectorAll('.emoji-btn').forEach(b=>b.classList.remove('selected')); el.classList.add('selected'); document.getElementById('selectedIcon').value=v; }
