// =========================
// 유일한 상태 관리자 — 다른 스크립트는 State API로만 접근
// =========================

window.State = (function () {
  var API_BASE = window.location.origin;

  // answerVersions: 새로고침 복원
  var answerVersionsRaw = {};
  try {
    var raw = sessionStorage.getItem('mellow_answerVersions');
    if (raw) {
      var parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') answerVersionsRaw = parsed;
    }
  } catch (_) { /* ignore */ }

  window.addEventListener('beforeunload', function () {
    try {
      if (answerVersionsRaw && typeof answerVersionsRaw === 'object')
        sessionStorage.setItem('mellow_answerVersions', JSON.stringify(answerVersionsRaw));
    } catch (_) { /* ignore */ }
  });

  var data = {
    AUTH_TOKEN: localStorage.getItem('auth_token') || localStorage.getItem('authToken') || null,
    CURRENT_USER: null,
    IS_GUEST_MODE: false,
    isAdmin: false,
    IS_ADMIN: false,
    CURRENT_SESSION_ID: null,
    CURRENT_FOLDER_ID: null,
    CURRENT_FOLDER: null,
    TEMP_SESSION_ID: null,
    FOLDERS: [],
    SIDEBAR_COLLAPSED: false,
    CURRENT_MODE: 'auto',
    abortController: null,
    isGenerating: false,
    isRegenerating: false,
    requestStartTime: 0,
    answerArchive: {},
    answerVersions: answerVersionsRaw,
    loopDetectionBuffer: '',
    loopDetectionThreshold: 3,
    isEditMode: false,
    editWarningBar: null,
    VRAM_STATUS: { used: 0, total: 0, percent: 0, lastUpdate: null },
    VRAM_POLL_INTERVAL: null,
    IMAGE_GENERATION_PENDING: false,
    MELLOW_LINK_EXPANDED: true,
    AVATAR_STATUS: { connected: false, port_active: false, relay_connected: false, last_check: null },
    SECRETARY_FOLDER_ID: null,
    CURRENT_FOLDER_SETTINGS_ID: null
  };

  var EDIT_CONTEXT = Object.assign({
    active: false,
    originMessageId: null,
    originText: '',
    draftBeforeEdit: '',
    backupMessages: [],
    backupSessionId: null,
    backupFolderId: null,
    backupCreatedAt: null,
    canRestore: false
  }, {});

  // —— Getters (ref 반환: FOLDERS, answerVersions, answerArchive, EDIT_CONTEXT, VRAM_STATUS, AVATAR_STATUS) ——
  function getApiBase() { return API_BASE; }
  function setApiBase(v) { API_BASE = v; }
  function getAuthToken() { return data.AUTH_TOKEN; }
  function setAuthToken(v) {
    data.AUTH_TOKEN = v;
    if (v != null) {
      localStorage.setItem('auth_token', v);
      localStorage.setItem('authToken', v);
    } else {
      localStorage.removeItem('auth_token');
      localStorage.removeItem('authToken');
    }
  }
  function getCurrentUser() { return data.CURRENT_USER; }
  function setCurrentUser(v) { data.CURRENT_USER = v; }
  function getIsGuestMode() { return data.IS_GUEST_MODE; }
  function setIsGuestMode(v) { data.IS_GUEST_MODE = v; }
  function getIsAdmin() { return data.isAdmin; }
  function setIsAdmin(v) { data.isAdmin = v; data.IS_ADMIN = v; }
  function getCurrentSessionId() { return data.CURRENT_SESSION_ID; }
  function setCurrentSessionId(v) { data.CURRENT_SESSION_ID = v; }
  function getCurrentFolderId() { return data.CURRENT_FOLDER_ID; }
  function setCurrentFolderId(v) { data.CURRENT_FOLDER_ID = v; }
  function getCurrentFolder() { return data.CURRENT_FOLDER; }
  function setCurrentFolder(v) { data.CURRENT_FOLDER = v; }
  function getTempSessionId() { return data.TEMP_SESSION_ID; }
  function setTempSessionId(v) { data.TEMP_SESSION_ID = v; }
  function getFolders() { return data.FOLDERS; }
  function setFolders(v) { data.FOLDERS = v; }
  function getSidebarCollapsed() { return data.SIDEBAR_COLLAPSED; }
  function setSidebarCollapsed(v) { data.SIDEBAR_COLLAPSED = v; }
  function getCurrentMode() { return data.CURRENT_MODE; }
  function setCurrentMode(v) { data.CURRENT_MODE = v; }
  function getAbortController() { return data.abortController; }
  function setAbortController(v) { data.abortController = v; }
  function getIsGenerating() { return data.isGenerating; }
  function setIsGenerating(v) { data.isGenerating = v; }
  function getIsRegenerating() { return data.isRegenerating; }
  function setIsRegenerating(v) { data.isRegenerating = v; }
  function getRequestStartTime() { return data.requestStartTime; }
  function setRequestStartTime(v) { data.requestStartTime = v; }
  function getAnswerVersions() { return data.answerVersions; }
  function getAnswerArchive() { return data.answerArchive; }
  function getEditContext() { return EDIT_CONTEXT; }
  function getLoopDetectionBuffer() { return data.loopDetectionBuffer; }
  function setLoopDetectionBuffer(v) { data.loopDetectionBuffer = v; }
  function getLoopDetectionThreshold() { return data.loopDetectionThreshold; }
  function setLoopDetectionThreshold(v) { data.loopDetectionThreshold = v; }
  function getIsEditMode() { return data.isEditMode; }
  function setIsEditMode(v) { data.isEditMode = v; }
  function getEditWarningBar() { return data.editWarningBar; }
  function setEditWarningBar(v) { data.editWarningBar = v; }
  function getVRAMStatus() { return data.VRAM_STATUS; }
  function getVRAMPollInterval() { return data.VRAM_POLL_INTERVAL; }
  function setVRAMPollInterval(v) { data.VRAM_POLL_INTERVAL = v; }
  function getImageGenerationPending() { return data.IMAGE_GENERATION_PENDING; }
  function setImageGenerationPending(v) { data.IMAGE_GENERATION_PENDING = v; }
  function getMellowLinkExpanded() { return data.MELLOW_LINK_EXPANDED; }
  function setMellowLinkExpanded(v) { data.MELLOW_LINK_EXPANDED = v; }
  function getAvatarStatus() { return data.AVATAR_STATUS; }
  function getSecretaryFolderId() { return data.SECRETARY_FOLDER_ID; }
  function setSecretaryFolderId(v) { data.SECRETARY_FOLDER_ID = v; }
  function getCurrentFolderSettingsId() { return data.CURRENT_FOLDER_SETTINGS_ID; }
  function setCurrentFolderSettingsId(v) { data.CURRENT_FOLDER_SETTINGS_ID = v; }

  return {
    getApiBase: getApiBase,
    setApiBase: setApiBase,
    getAuthToken: getAuthToken,
    setAuthToken: setAuthToken,
    getCurrentUser: getCurrentUser,
    setCurrentUser: setCurrentUser,
    getIsGuestMode: getIsGuestMode,
    setIsGuestMode: setIsGuestMode,
    getIsAdmin: getIsAdmin,
    setIsAdmin: setIsAdmin,
    getCurrentSessionId: getCurrentSessionId,
    setCurrentSessionId: setCurrentSessionId,
    getCurrentFolderId: getCurrentFolderId,
    setCurrentFolderId: setCurrentFolderId,
    getCurrentFolder: getCurrentFolder,
    setCurrentFolder: setCurrentFolder,
    getTempSessionId: getTempSessionId,
    setTempSessionId: setTempSessionId,
    getFolders: getFolders,
    setFolders: setFolders,
    getSidebarCollapsed: getSidebarCollapsed,
    setSidebarCollapsed: setSidebarCollapsed,
    getCurrentMode: getCurrentMode,
    setCurrentMode: setCurrentMode,
    getAbortController: getAbortController,
    setAbortController: setAbortController,
    getIsGenerating: getIsGenerating,
    setIsGenerating: setIsGenerating,
    getIsRegenerating: getIsRegenerating,
    setIsRegenerating: setIsRegenerating,
    getRequestStartTime: getRequestStartTime,
    setRequestStartTime: setRequestStartTime,
    getAnswerVersions: getAnswerVersions,
    getAnswerArchive: getAnswerArchive,
    getEditContext: getEditContext,
    getLoopDetectionBuffer: getLoopDetectionBuffer,
    setLoopDetectionBuffer: setLoopDetectionBuffer,
    getLoopDetectionThreshold: getLoopDetectionThreshold,
    setLoopDetectionThreshold: setLoopDetectionThreshold,
    getIsEditMode: getIsEditMode,
    setIsEditMode: setIsEditMode,
    getEditWarningBar: getEditWarningBar,
    setEditWarningBar: setEditWarningBar,
    getVRAMStatus: getVRAMStatus,
    getVRAMPollInterval: getVRAMPollInterval,
    setVRAMPollInterval: setVRAMPollInterval,
    getImageGenerationPending: getImageGenerationPending,
    setImageGenerationPending: setImageGenerationPending,
    getMellowLinkExpanded: getMellowLinkExpanded,
    setMellowLinkExpanded: setMellowLinkExpanded,
    getAvatarStatus: getAvatarStatus,
    getSecretaryFolderId: getSecretaryFolderId,
    setSecretaryFolderId: setSecretaryFolderId,
    getCurrentFolderSettingsId: getCurrentFolderSettingsId,
    setCurrentFolderSettingsId: setCurrentFolderSettingsId
  };
})();
