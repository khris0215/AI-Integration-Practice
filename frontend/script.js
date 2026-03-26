const API_BASE = 'http://localhost:8000/api';
const PROMPT_DRAFT_KEY = 'landing-prompt-draft';
const ERROR_CACHE_KEY = 'landing-error-message';
const TEMPLATE_FILENAME_KEY = 'landing-template-filename';
const TEMPLATE_SELECTION_KEY = 'landing-template-selection';
const ACTIVE_CONVERSATION_KEY = 'landing-active-conversation-id';
const APP_INIT_GUARD_KEY = '__landingAppInitialized';
const LOGIN_FRESH_FLAG = 'fresh';
const QUERY_TIMEOUT_MS = 180000;
const FRONTEND_TRACE_ENABLED = true;

if (typeof window._backendHealthy !== 'boolean') {
    window._backendHealthy = false;
}

let initCount = 0;
const INIT_STACK = [];
let requestTraceCounter = 0;
let flowTraceCounter = 0;

function diagTimestamp() {
    return new Date().toISOString();
}

function diagLog(level, message, ...args) {
    const fn = console[level] || console.log;
    fn(`[${diagTimestamp()}] ${message}`, ...args);
}

function shouldPersistError(message) {
    const normalized = String(message || '').toLowerCase();
    if (!normalized) {
        return false;
    }
    if (normalized.includes('backend is still starting')) {
        return false;
    }
    if (normalized.includes('retrying')) {
        return false;
    }
    if (normalized.includes('temporarily unreachable')) {
        return false;
    }
    if (normalized.includes('request timed out')) {
        return false;
    }
    return true;
}

function nextTraceId(prefix) {
    if (prefix === 'req') {
        requestTraceCounter += 1;
        return `req-${requestTraceCounter}`;
    }
    flowTraceCounter += 1;
    return `${prefix}-${flowTraceCounter}`;
}

function summarizePayload(body) {
    if (body === null || body === undefined) {
        return { kind: 'none', size: 0 };
    }
    if (typeof body === 'string') {
        return { kind: 'string', size: body.length };
    }
    if (body instanceof FormData) {
        const entries = [];
        for (const [key, value] of body.entries()) {
            if (value instanceof File) {
                entries.push({ key, type: 'file', name: value.name, size: value.size });
            } else {
                entries.push({ key, type: 'field', size: String(value || '').length });
            }
        }
        return { kind: 'form-data', fields: entries.length, entries };
    }
    return { kind: typeof body, size: 0 };
}

function traceState(label, details = {}) {
    if (!FRONTEND_TRACE_ENABLED) {
        return;
    }
    diagLog('log', `[STATE] ${label}`, details);
}

async function tracedFetch(url, options = {}, meta = {}) {
    const traceId = meta.traceId || nextTraceId('req');
    const method = options.method || 'GET';
    const payload = summarizePayload(options.body);
    const startedAt = performance.now();

    diagLog('log', `[HTTP ${traceId}] start`, {
        method,
        url,
        payload,
        meta,
    });

    try {
        const response = await fetch(url, options);
        const durationMs = Math.round(performance.now() - startedAt);
        const serverTraceId = response.headers.get('x-trace-id') || null;
        diagLog('log', `[HTTP ${traceId}] end`, {
            method,
            url,
            status: response.status,
            ok: response.ok,
            durationMs,
            serverTraceId,
            meta,
        });
        return response;
    } catch (error) {
        const durationMs = Math.round(performance.now() - startedAt);
        diagLog('error', `[HTTP ${traceId}] failed`, {
            method,
            url,
            durationMs,
            message: error?.message,
            name: error?.name,
            meta,
        });
        throw error;
    }
}

async function extractApiErrorMessage(response, fallbackMessage) {
    const fallback = fallbackMessage || `API error: ${response?.status}`;
    if (!response) {
        return fallback;
    }

    try {
        const contentType = String(response.headers.get('content-type') || '').toLowerCase();
        if (contentType.includes('application/json')) {
            const payload = await response.json();
            const detail = String(payload?.detail || payload?.error || '').trim();
            if (detail) {
                return detail;
            }
            return fallback;
        }

        const text = String(await response.text() || '').trim();
        if (!text) {
            return fallback;
        }

        if (text.startsWith('{') && text.endsWith('}')) {
            try {
                const payload = JSON.parse(text);
                const detail = String(payload?.detail || payload?.error || '').trim();
                if (detail) {
                    return detail;
                }
            } catch (_) {
                // Fall back to plain text below.
            }
        }

        return text;
    } catch (_) {
        return fallback;
    }
}

function logInit(reason) {
    initCount += 1;
    const stack = new Error().stack || '';
    const entry = {
        count: initCount,
        reason,
        stack,
        time: Date.now(),
    };
    INIT_STACK.push(entry);
    window.__landingInitDiagnostics = {
        initCount,
        history: [...INIT_STACK],
    };
    diagLog('warn', `[INIT] #${initCount} reason=${reason}`, entry);
}

document.addEventListener('DOMContentLoaded', () => {
    logInit('DOMContentLoaded');

    if (window[APP_INIT_GUARD_KEY]) {
        diagLog('warn', 'Landing app already initialized. Skipping duplicate bootstrap.');
        return;
    }
    window[APP_INIT_GUARD_KEY] = true;

    const scriptCount = document.querySelectorAll('script[src$="script.js"]').length;
    diagLog('warn', `script.js tag count = ${scriptCount}`);

    window.addEventListener('beforeunload', () => {
        diagLog('warn', 'Page is being unloaded/reloaded.');
    });

    window.addEventListener('error', (event) => {
        diagLog('error', 'Unhandled window error:', event.message, event.error);
    });

    window.addEventListener('unhandledrejection', (event) => {
        diagLog('error', 'Unhandled promise rejection:', event.reason);
    });

    const promptForm = document.getElementById('prompt-form');
    const promptInput = document.getElementById('prompt-input');
    const resultContainer = document.getElementById('result-container');
    let loadingIndicator = null;
    let errorContainer = document.getElementById('error-container');
    const submitButton = document.getElementById('submit-button');
    const templateSelect = document.getElementById('template-select');
    const templateFileInput = document.getElementById('template-file');
    const conversationList = document.getElementById('conversation-list');
    const newChatButton = document.getElementById('new-chat-btn');
    const submitLabel = submitButton?.querySelector('[data-role="label"]');
    const submitSpinner = submitButton?.querySelector('[data-role="spinner"]');
    const backendStatus = document.getElementById('backend-status');
    const feedPlaceholder = document.getElementById('feed-placeholder');
    const sidebar = document.querySelector('[data-sidebar]');
    const sidebarToggleButtons = document.querySelectorAll('[data-sidebar-toggle]');
    const sidebarBrands = document.querySelectorAll('[data-sidebar-brand]');
    const sidebarLinks = document.querySelectorAll('.sidebar-nav .sidebar-link');
    const SIDEBAR_STORAGE_KEY = 'landing-sidebar-collapsed';
    const collapseQuery = window.matchMedia('(max-width: 1024px)');
    const pageFlowId = nextTraceId('flow');

    let userPreferenceLocked = false;
    let hideSidebarTooltip = () => {};
    let activeController = null;
    let isProcessing = false;
    let activeOperation = null;
    let backendReady = Boolean(window._backendHealthy);
    let currentConversationId = null;
    let conversations = [];
    let transientMessageId = 0;
    let healthPollStarted = false;
    let healthPollPromise = null;
    let loadingProgressTrack = null;
    let loadingProgressFill = null;
    let loadingElapsedText = null;
    let availableTemplates = [];

    if (promptForm) {
        // Guard against accidental page reload if later init code throws.
        promptForm.addEventListener('submit', (event) => {
            event.preventDefault();
        }, { capture: true });
    }

    traceState('dom-content-loaded', {
        pageFlowId,
        backendReady,
        hasPromptForm: Boolean(promptForm),
        hasResultContainer: Boolean(resultContainer),
        hasBackendStatus: Boolean(backendStatus),
    });

    if (backendStatus && !backendReady) {
        backendStatus.textContent = 'Checking backend status...';
        backendStatus.classList.remove('hidden');
    }

    ensureStatusContainers();
    if (consumeFreshSessionFlag()) {
        startFreshSession();
    }
    restoreDraftState();
    try {
        initSidebar();
        initSidebarTooltips();
    } catch (error) {
        console.warn('Sidebar init skipped due to runtime error:', error);
    }

    void initializeApp();

    async function initializeApp() {
        logInit('initializeApp');
        traceState('initialize-app-start', { pageFlowId, backendReady });
        const isHealthy = await startHealthPollOnce();
        if (!isHealthy) {
            traceState('initialize-app-backend-not-healthy', { pageFlowId });
            return;
        }
        await loadTemplateOptions();
        await refreshConversations();
        traceState('initialize-app-done', {
            pageFlowId,
            backendReady,
            currentConversationId,
            conversationCount: Array.isArray(conversations) ? conversations.length : -1,
        });
    }

    function startHealthPollOnce() {
        if (healthPollStarted && healthPollPromise) {
            traceState('health-poll-reused', { pageFlowId });
            return healthPollPromise;
        }
        healthPollStarted = true;
        traceState('health-poll-started', { pageFlowId });
        healthPollPromise = pollBackendHealth();
        return healthPollPromise;
    }

    if (promptInput) {
        promptInput.addEventListener('input', () => {
            safeSet(PROMPT_DRAFT_KEY, promptInput.value);
        });

        promptInput.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || event.shiftKey) {
                return;
            }

            event.preventDefault();
            promptForm?.requestSubmit();
        });
    }

    if (templateFileInput) {
        templateFileInput.addEventListener('change', () => {
            const selectedName = templateFileInput.files && templateFileInput.files.length
                ? templateFileInput.files[0].name
                : '';
            safeSet(TEMPLATE_FILENAME_KEY, selectedName);
        });
    }

    if (templateSelect) {
        templateSelect.addEventListener('change', () => {
            const selectedValue = String(templateSelect.value || '').trim();
            safeSet(TEMPLATE_SELECTION_KEY, selectedValue);
        });
    }

    if (newChatButton) {
        newChatButton.addEventListener('click', async () => {
            const flowId = nextTraceId('new-chat');
            traceState('new-chat-click', { flowId, backendReady, activeOperation, currentConversationId });
            if (!backendReady || activeOperation) {
                traceState('new-chat-blocked', { flowId, backendReady, activeOperation });
                return;
            }
            currentConversationId = null;
            persistActiveConversation(null);
            renderMessages([]);
            renderConversationList();
            showError('');
            promptInput?.focus();
            traceState('new-chat-finished', { flowId, currentConversationId });
        });
    }

    if (promptForm && promptInput) {
        promptForm.addEventListener('submit', async (event) => {
            const flowId = nextTraceId('query');
            try {
                event.preventDefault();
                diagLog('log', 'submit start', {
                    flowId,
                    activeOperation,
                    backendReady,
                    currentConversationId,
                });
                if (activeOperation === 'submit') {
                    traceState('query-cancel-requested', { flowId, currentConversationId });
                    cancelActiveRequest();
                    return;
                }
                if (activeOperation && activeOperation !== 'submit') {
                    showError('Another request is in progress. Please wait for it to finish.');
                    return;
                }

                const prompt = promptInput.value.trim();
                let selectedTemplateId = String(templateSelect?.value || '').trim();
                const uploadedTemplateFile = templateFileInput?.files?.[0] || null;
                const hasUploadedTemplate = Boolean(uploadedTemplateFile);
                traceState('query-validated-input', {
                    flowId,
                    promptLength: prompt.length,
                    backendReady,
                    activeOperation,
                    currentConversationId,
                    selectedTemplateId,
                    hasUploadedTemplate,
                });

                if (!backendReady) {
                    showError('Backend is still starting. Please wait until status shows connected.');
                    return;
                }

                if (!prompt) {
                    showError('Please describe the fraud scenario you want to generate.');
                    return;
                }

                showError('');
                activeOperation = 'submit';
                showLoading(true, 'submit');
                safeSet(PROMPT_DRAFT_KEY, prompt);
                appendMessageToFeed({ role: 'user', content: prompt, timestamp: new Date().toISOString() });
                promptInput.value = '';
                safeSet(PROMPT_DRAFT_KEY, '');

                const inferredIntent = inferDeterministicIntent({ prompt, selectedTemplateId, hasUploadedTemplate });
                let intent = inferredIntent.intent;
                let intentConfidence = inferredIntent.confidence;
                let intentReason = inferredIntent.reason;

                const looksTemplateRelated = /\b(fill|template|populate|report|cfir)\b/i.test(prompt);
                if (intent === 'query' && looksTemplateRelated && !selectedTemplateId && !hasUploadedTemplate) {
                    const classified = await classifyIntentFallback({
                        prompt,
                        hasSelectedTemplate: false,
                        hasUploadedTemplate: false,
                    });
                    intent = classified.intent;
                    intentConfidence = classified.confidence;
                    intentReason = classified.reason;
                }

                traceState('intent-routing', {
                    flowId,
                    intent,
                    intentConfidence,
                    intentReason,
                    selectedTemplateId,
                    hasUploadedTemplate,
                });

                if (intent !== 'query' && intentConfidence < 0.65) {
                    appendMessageToFeed({
                        role: 'assistant',
                        content: 'I can either answer your question or fill a template. Please confirm what you want me to do.',
                        timestamp: new Date().toISOString(),
                    });
                    showLoading(false, 'submit');
                    activeOperation = null;
                    return;
                }

                if (intent === 'fill_template' && !selectedTemplateId && !hasUploadedTemplate) {
                    const inferredTemplateId = resolveTemplateIdFromPrompt(prompt, availableTemplates);
                    if (inferredTemplateId) {
                        selectedTemplateId = inferredTemplateId;
                        if (templateSelect) {
                            templateSelect.value = inferredTemplateId;
                        }
                        safeSet(TEMPLATE_SELECTION_KEY, inferredTemplateId);
                        traceState('template-auto-resolved', {
                            flowId,
                            inferredTemplateId,
                        });
                    }
                }

                if (intent === 'fill_template' && !selectedTemplateId && !hasUploadedTemplate) {
                    appendMessageToFeed({
                        role: 'assistant',
                        content: 'I can fill a template, but none is selected. Choose a template from the selector or upload one for this prompt. I can also continue in Q&A mode if you prefer.',
                        timestamp: new Date().toISOString(),
                    });
                    showLoading(false, 'submit');
                    activeOperation = null;
                    return;
                }

                const pendingAssistantId = appendTypingMessage(intent === 'fill_template' ? 'AI is filling the template...' : 'AI is thinking...');

                const controller = new AbortController();
                activeController = controller;
                traceState('query-controller-created', { flowId });

                try {
                    diagLog('log', 'submit fetch start', {
                        promptLength: prompt.length,
                        currentConversationId,
                        intent,
                    });
                    let response;
                    if (intent === 'fill_template') {
                        response = await fetchWithStartupRetry(
                            (requestSignal, requestTraceId) => {
                                const formData = new FormData();
                                formData.append('prompt', prompt);
                                if (Number.isFinite(currentConversationId)) {
                                    formData.append('conversation_id', String(currentConversationId));
                                }
                                if (selectedTemplateId) {
                                    formData.append('template_id', selectedTemplateId);
                                }
                                if (uploadedTemplateFile) {
                                    formData.append('file', uploadedTemplateFile);
                                }
                                return tracedFetch(`${API_BASE}/fill-template`, {
                                    method: 'POST',
                                    body: formData,
                                    signal: requestSignal,
                                }, {
                                    flowId,
                                    requestTraceId,
                                    operation: 'fill-template',
                                    conversationId: currentConversationId,
                                });
                            },
                            controller.signal,
                            4,
                            { flowId, operation: 'fill-template' },
                        );
                    } else {
                        response = await fetchWithStartupRetry(
                            (requestSignal, requestTraceId) => tracedFetch(`${API_BASE}/query`, {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                },
                                body: JSON.stringify({
                                    prompt,
                                    conversation_id: currentConversationId,
                                    temperature: 0.2,
                                }),
                                signal: requestSignal,
                            }, {
                                flowId,
                                requestTraceId,
                                operation: 'query',
                                conversationId: currentConversationId,
                            }),
                            controller.signal,
                            4,
                            { flowId, operation: 'query' },
                        );
                    }

                    if (!response.ok) {
                        const apiError = await extractApiErrorMessage(response, `API error: ${response.status}`);
                        throw new Error(apiError);
                    }

                    diagLog('log', 'submit fetch success', { status: response.status });

                    const data = await response.json();
                    traceState('query-response-json', {
                        flowId,
                        keys: Object.keys(data || {}),
                        hasAnswer: Boolean(data?.answer),
                        hasConversationId: typeof data?.conversation_id === 'number',
                    });
                    if (typeof data.conversation_id === 'number') {
                        const previousConversationId = currentConversationId;
                        currentConversationId = data.conversation_id;
                        persistActiveConversation(currentConversationId);
                        diagLog('log', 'conversation id changed after query', {
                            previousConversationId,
                            currentConversationId,
                        });
                    }

                    removeTransientMessage(pendingAssistantId);
                    appendMessageToFeed({
                        role: 'assistant',
                        content: data.answer || 'Response received.',
                        timestamp: new Date().toISOString(),
                        attachment_id: data.attachment_id,
                        attachment_filename: data.attachment_filename,
                    });

                    await refreshConversations(currentConversationId);
                    if (uploadedTemplateFile && templateFileInput) {
                        templateFileInput.value = '';
                        safeSet(TEMPLATE_FILENAME_KEY, '');
                    }
                    traceState('query-finished-success', { flowId, currentConversationId });
                } catch (error) {
                    diagLog('error', 'submit fetch error', error);
                    removeTransientMessage(pendingAssistantId);
                    if (error.name === 'AbortError') {
                        showError('Prompt cancelled.');
                    } else {
                        showError(error.message || 'Unable to reach the CFIR API.');
                    }
                    traceState('query-finished-error', {
                        flowId,
                        name: error?.name,
                        message: error?.message,
                    });
                } finally {
                    showLoading(false, 'submit');
                    if (activeController === controller) {
                        activeController = null;
                    }
                    activeOperation = null;
                    traceState('query-finally', { flowId, activeOperation, hasActiveController: Boolean(activeController) });
                }
            } catch (error) {
                diagLog('error', 'Submit handler error:', error);
                showError('An unexpected error occurred. Please try again.');
                showLoading(false, 'submit');
                activeOperation = null;
                activeController = null;
                traceState('query-handler-crash', { flowId, message: error?.message });
            }
        });
    }

    async function refreshConversations(preferredId = null) {
        diagLog('log', 'refreshConversations start', {
            preferredId,
            currentConversationId,
            existingConversationCount: Array.isArray(conversations) ? conversations.length : 0,
        });

        try {
            const response = await tracedFetch(`${API_BASE}/conversations`, {
                method: 'GET',
                cache: 'no-store',
            }, {
                flow: 'refresh-conversations',
                preferredId,
                currentConversationId,
            });
            if (!response.ok) {
                diagLog('warn', 'refreshConversations fetch failed', { status: response.status });
                showError(`Unable to load conversations (${response.status})`);
                return;
            }

            const fetchedConversations = await response.json();
            if (!Array.isArray(fetchedConversations)) {
                diagLog('warn', 'refreshConversations received non-array payload');
                return;
            }

            conversations = fetchedConversations;
            const restoredId = readPersistedActiveConversation();
            const targetId = preferredId ?? currentConversationId ?? restoredId;
            const hasTarget = conversations.some((conv) => conv.id === targetId);

            if (hasTarget) {
                currentConversationId = targetId;
            } else {
                currentConversationId = null;
            }

            persistActiveConversation(currentConversationId);
            renderConversationList();

            if (currentConversationId !== null) {
                await loadConversationMessages(currentConversationId);
            } else if (conversations.length === 0) {
                renderMessages([], { allowClear: true, reason: 'no-conversations' });
            }
            diagLog('log', 'refreshConversations done', {
                currentConversationId,
                conversationCount: conversations.length,
            });
        } catch (error) {
            diagLog('error', 'refreshConversations error', error);
            showError(error.message || 'Failed to load conversation history.');
        }
    }

    async function loadConversationMessages(conversationId) {
        const response = await tracedFetch(`${API_BASE}/conversations/${conversationId}/messages`, {
            method: 'GET',
            cache: 'no-store',
        }, {
            flow: 'load-conversation-messages',
            conversationId,
        });

        if (!response.ok) {
            throw new Error(`Unable to load messages (${response.status})`);
        }

        const messages = await response.json();
        diagLog('log', 'loadConversationMessages success', {
            conversationId,
            messageCount: Array.isArray(messages) ? messages.length : -1,
        });
        renderMessages(messages, { conversationId, allowClear: true, reason: 'load-conversation' });
    }

    async function removeConversation(conversationId) {
        const response = await tracedFetch(`${API_BASE}/conversations/${conversationId}`, {
            method: 'DELETE',
        }, {
            flow: 'remove-conversation',
            conversationId,
        });

        if (!response.ok) {
            throw new Error(`Unable to delete conversation (${response.status})`);
        }
    }

    function renderConversationList() {
        if (!conversationList) {
            return;
        }

        conversationList.innerHTML = '';

        if (!Array.isArray(conversations) || conversations.length === 0) {
            const item = document.createElement('li');
            item.className = 'chat-thread__empty';
            item.textContent = 'No conversations yet. Start with New Chat.';
            conversationList.appendChild(item);
            return;
        }

        conversations.forEach((conv) => {
            const li = document.createElement('li');
            li.className = 'chat-thread__row';

            const itemButton = document.createElement('button');
            itemButton.type = 'button';
            itemButton.className = 'chat-thread__item';
            if (conv.id === currentConversationId) {
                itemButton.classList.add('is-active');
            }

            const title = (conv.title || '').trim() || `Conversation ${conv.id}`;
            const preview = truncateForPreview(conv.preview || '', 58);

            itemButton.innerHTML = `
                <div class="chat-thread__meta">
                    <span class="chat-thread__label">${escapeHtml(title)}</span>
                </div>
                <p class="chat-thread__preview">${escapeHtml(preview || 'No messages yet')}</p>
            `;

            itemButton.addEventListener('click', async () => {
                if (activeOperation) {
                    return;
                }
                currentConversationId = conv.id;
                persistActiveConversation(currentConversationId);
                renderConversationList();
                try {
                    await loadConversationMessages(conv.id);
                    showError('');
                } catch (error) {
                    showError(error.message || 'Failed to load conversation messages.');
                }
            });

            const deleteButton = document.createElement('button');
            deleteButton.type = 'button';
            deleteButton.className = 'chat-thread__delete';
            deleteButton.setAttribute('aria-label', `Delete conversation ${title}`);
            deleteButton.innerHTML = `
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                    <path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm1 6h2v9h-2V9Zm4 0h2v9h-2V9ZM7 9h2v9H7V9Z" fill="currentColor"></path>
                </svg>
            `;

            deleteButton.addEventListener('click', async (event) => {
                event.stopPropagation();
                if (activeOperation) {
                    return;
                }
                const confirmDelete = window.confirm('Delete this conversation and all its messages?');
                if (!confirmDelete) {
                    return;
                }

                try {
                    await removeConversation(conv.id);
                    if (currentConversationId === conv.id) {
                        currentConversationId = null;
                        persistActiveConversation(null);
                    }
                    await refreshConversations();
                    showError('');
                } catch (error) {
                    showError(error.message || 'Failed to delete conversation.');
                }
            });

            li.append(itemButton, deleteButton);
            conversationList.appendChild(li);
        });
    }

    function renderMessages(messages, options = {}) {
        if (!resultContainer) {
            return;
        }

        const {
            allowClear = false,
            conversationId = currentConversationId,
            reason = 'unspecified',
        } = options;

        if (!Array.isArray(messages)) {
            diagLog('warn', 'renderMessages skipped: payload is not an array', { reason, conversationId });
            return;
        }

        traceState('render-messages-start', {
            reason,
            conversationId,
            allowClear,
            incomingCount: messages.length,
            currentConversationId,
        });

        if (messages.length === 0 && currentConversationId !== null && !allowClear) {
            diagLog('warn', 'renderMessages skipped empty clear to preserve current conversation', {
                currentConversationId,
                attemptedConversationId: conversationId,
                reason,
            });
            return;
        }

        resultContainer.innerHTML = '';
        traceState('render-messages-cleared-container', {
            reason,
            conversationId,
            previousCount: resultContainer.childElementCount,
        });

        if (messages.length === 0) {
            updateFeedPlaceholder();
            return;
        }

        messages.forEach((message) => {
            appendMessageToFeed(message);
        });

        diagLog('log', 'renderMessages complete', {
            conversationId,
            messageCount: messages.length,
            reason,
        });

        resultContainer.scrollTop = resultContainer.scrollHeight;
        updateFeedPlaceholder();
    }

    function appendMessageToFeed(message) {
        if (!resultContainer) {
            return null;
        }

        traceState('append-message', {
            role: message.role,
            isTyping: Boolean(message.isTyping),
            hasAttachment: Boolean(message.attachment_id),
            contentSize: String(message.content || '').length,
            conversationId: currentConversationId,
        });

        const role = message.role === 'assistant' ? 'assistant' : 'user';
        const wrapper = document.createElement('article');
        wrapper.className = `chat-message chat-message--${role}`;
        if (message._transientId) {
            wrapper.setAttribute('data-transient-id', String(message._transientId));
        }

        const roleLabel = document.createElement('p');
        roleLabel.className = 'chat-message__role';
        roleLabel.textContent = role === 'assistant' ? 'Assistant' : 'You';

        const bubble = document.createElement('div');
        bubble.className = `chat-message__bubble${message.isTyping ? ' chat-message__bubble--typing' : ''}`;
        if (message.isTyping) {
            bubble.innerHTML = `
                <span class="chat-message__typing-label">${escapeHtml(message.content || 'Processing...')}</span>
                <span class="typing-dots" aria-hidden="true">
                    <span></span><span></span><span></span>
                </span>
            `;
        } else {
            bubble.textContent = message.content || '';
        }

        wrapper.append(roleLabel, bubble);

        if (message.attachment_id) {
            const attachment = document.createElement('a');
            attachment.className = 'chat-message__attachment';
            attachment.href = `${API_BASE}/attachments/${message.attachment_id}/download`;
            attachment.textContent = `Download: ${message.attachment_filename || 'filled-template'}`;
            attachment.setAttribute('download', message.attachment_filename || 'filled-template');
            wrapper.appendChild(attachment);
        }

        const meta = document.createElement('p');
        meta.className = 'chat-message__time';
        meta.textContent = formatTimestamp(message.timestamp);
        wrapper.appendChild(meta);

        resultContainer.appendChild(wrapper);
        resultContainer.scrollTop = resultContainer.scrollHeight;
        updateFeedPlaceholder();
        return wrapper;
    }

    function appendTypingMessage(labelText) {
        transientMessageId += 1;
        const id = `typing-${transientMessageId}`;
        appendMessageToFeed({
            role: 'assistant',
            content: labelText,
            isTyping: true,
            timestamp: new Date().toISOString(),
            _transientId: id,
        });
        return id;
    }

    function removeTransientMessage(id) {
        if (!resultContainer || !id) {
            return;
        }
        const target = resultContainer.querySelector(`[data-transient-id="${id}"]`);
        if (target) {
            target.remove();
            updateFeedPlaceholder();
        }
    }

    function truncateForPreview(value, maxLength = 60) {
        const normalized = String(value || '').replace(/\s+/g, ' ').trim();
        if (!normalized) {
            return '';
        }
        if (normalized.length <= maxLength) {
            return normalized;
        }
        return `${normalized.slice(0, maxLength - 1)}...`;
    }

    function cancelActiveRequest() {
        if (!activeController) {
            return;
        }
        activeController.abort();
    }

    function showLoading(isLoading, operation = 'query') {
        traceState('show-loading', {
            isLoading,
            operation,
            backendReady,
            activeOperation,
            currentConversationId,
        });
        toggleSubmitState(isLoading, operation);
        updateFeedPlaceholder();
    }

    function toggleSubmitState(isLoading, operation) {
        isProcessing = isLoading;
        if (submitButton) {
            const isCancellableOperation = operation === 'query' || operation === 'submit';
            submitButton.setAttribute('data-state', isLoading && isCancellableOperation ? 'cancel' : 'idle');
            submitButton.setAttribute('aria-busy', String(isLoading && isCancellableOperation));
            submitButton.disabled = !backendReady || Boolean(isLoading && !isCancellableOperation);
            if (submitSpinner) {
                submitSpinner.classList.toggle('hidden', !(isLoading && isCancellableOperation));
            }
            if (submitLabel) {
                submitLabel.textContent = isLoading && isCancellableOperation ? 'Cancel' : 'Send prompt';
            }
        }

        if (templateFileInput) {
            templateFileInput.disabled = !backendReady || isLoading;
        }

        if (newChatButton) {
            newChatButton.disabled = !backendReady || isLoading;
        }
    }

    async function pollBackendHealth() {
        logInit('pollBackendHealth start');
        traceState('poll-backend-health-enter', { backendReady, windowBackendHealthy: window._backendHealthy });
        if (window._backendHealthy || backendReady) {
            window._backendHealthy = true;
            backendReady = true;
            if (backendStatus) {
                backendStatus.classList.add('hidden');
            }
            diagLog('warn', 'pollBackendHealth skipped because backendReady=true');
            return true;
        }

        const maxAttempts = 15;
        const intervalMs = 2000;

        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            try {
                if (!window._backendHealthy && backendStatus) {
                    backendStatus.textContent = `Checking backend availability... (${attempt}/${maxAttempts})`;
                }
                const response = await tracedFetch(`${API_BASE}/health`, {
                    method: 'GET',
                    cache: 'no-store',
                }, {
                    flow: 'poll-backend-health',
                    attempt,
                    maxAttempts,
                });
                if (response.ok) {
                    const payload = await response.json();
                    traceState('poll-backend-health-payload', { attempt, payload });
                    if (payload && payload.status === 'healthy' && payload.ready === true) {
                        window._backendHealthy = true;
                        setBackendReady(true, `Backend connected (v${payload.version || 'unknown'}).`);
                        if (backendStatus) {
                            backendStatus.classList.add('hidden');
                        }
                        return true;
                    }
                }
            } catch (_) {
                // Ignore transient failures during startup and continue polling.
            }

            if (attempt < maxAttempts) {
                await wait(intervalMs);
            }
        }

        if (backendStatus) {
            backendStatus.textContent = 'Backend unavailable. Start the API server and refresh this page.';
            backendStatus.classList.remove('hidden');
        }
        showError('Backend health check failed after multiple attempts.');
        return false;
    }

    function setBackendReady(isReady, message) {
        traceState('set-backend-ready-call', {
            isReady,
            message,
            backendReady,
            windowBackendHealthy: window._backendHealthy,
        });
        if (!isReady && window._backendHealthy) {
            diagLog('warn', 'Ignored attempt to mark backend unready after healthy lock', { message });
            return;
        }

        backendReady = isReady;
        if (isReady) {
            window._backendHealthy = true;
        }

        if (backendStatus) {
            backendStatus.textContent = message;
            backendStatus.classList.toggle('hidden', Boolean(window._backendHealthy || isReady));
            backendStatus.classList.remove('border-amber-200', 'bg-amber-50', 'text-amber-700', 'dark:border-amber-400/30', 'dark:bg-amber-400/10', 'dark:text-amber-200');
            backendStatus.classList.remove('border-emerald-200', 'bg-emerald-50', 'text-emerald-700', 'dark:border-emerald-400/30', 'dark:bg-emerald-400/10', 'dark:text-emerald-200');
            backendStatus.classList.add(
                isReady ? 'border-emerald-200' : 'border-amber-200',
                isReady ? 'bg-emerald-50' : 'bg-amber-50',
                isReady ? 'text-emerald-700' : 'text-amber-700',
                isReady ? 'dark:border-emerald-400/30' : 'dark:border-amber-400/30',
                isReady ? 'dark:bg-emerald-400/10' : 'dark:bg-amber-400/10',
                isReady ? 'dark:text-emerald-200' : 'dark:text-amber-200',
            );
        }

        if (submitButton) {
            submitButton.disabled = !isReady;
        }
        if (templateFileInput) {
            templateFileInput.disabled = !isReady;
        }
        if (newChatButton) {
            newChatButton.disabled = !isReady;
        }
    }

    function showError(message) {
        traceState('show-error', {
            hasMessage: Boolean(message),
            messageLength: String(message || '').length,
            preview: String(message || '').slice(0, 120),
            activeOperation,
            backendReady,
        });
        if (!errorContainer) {
            return;
        }
        if (!message) {
            errorContainer.textContent = '';
            errorContainer.classList.add('hidden');
            safeSet(ERROR_CACHE_KEY, '');
            updateFeedPlaceholder();
            return;
        }

        errorContainer.textContent = message;
        errorContainer.classList.remove('hidden');
        safeSet(ERROR_CACHE_KEY, shouldPersistError(message) ? message : '');
        updateFeedPlaceholder();
    }

    async function fetchWithStartupRetry(requestFactory, signal, maxAttempts = 4, meta = {}) {
        if (typeof maxAttempts === 'object' && maxAttempts !== null) {
            meta = maxAttempts;
            maxAttempts = 4;
        }

        const normalizedAttempts = Number.isFinite(Number(maxAttempts)) && Number(maxAttempts) > 0
            ? Math.floor(Number(maxAttempts))
            : 4;

        let lastError = null;
        for (let attempt = 1; attempt <= normalizedAttempts; attempt += 1) {
            const requestTraceId = nextTraceId('req');
            traceState('fetch-with-startup-retry-attempt', {
                ...meta,
                attempt,
                maxAttempts: normalizedAttempts,
                requestTraceId,
                aborted: Boolean(signal?.aborted),
            });
            if (signal?.aborted) {
                throw new DOMException('The operation was aborted.', 'AbortError');
            }

            try {
                const response = await fetchWithTimeout(
                    (requestSignal) => requestFactory(requestSignal, requestTraceId),
                    QUERY_TIMEOUT_MS,
                    signal,
                );
                if (response.ok) {
                    traceState('fetch-with-startup-retry-success', {
                        ...meta,
                        attempt,
                        requestTraceId,
                        status: response.status,
                    });
                    return response;
                }

                if ([502, 503, 504].includes(response.status) && attempt < normalizedAttempts) {
                    showError(`Backend is waking up... retry ${attempt}/${normalizedAttempts - 1}`);
                    await wait(900 * attempt);
                    continue;
                }

                return response;
            } catch (error) {
                if (error?.name === 'AbortError') {
                    throw error;
                }

                lastError = error;
                traceState('fetch-with-startup-retry-error', {
                    ...meta,
                    attempt,
                    requestTraceId,
                    name: error?.name,
                    message: error?.message,
                });
                if (attempt < normalizedAttempts) {
                    const statusHint = await detectBackendStatusHint();
                    showError(`${statusHint} Retrying... (${attempt}/${normalizedAttempts - 1})`);
                    await wait(900 * attempt);
                    continue;
                }
            }
        }

        const fallbackMessage = 'Backend is still starting. Your draft is preserved - try again in a few seconds.';
        throw new Error(lastError?.message || fallbackMessage);
    }

    async function fetchWithTimeout(requestFactory, timeoutMs, outerSignal) {
        const timeoutController = new AbortController();
        let timeoutHandle = null;

        const relayAbort = () => timeoutController.abort();
        if (outerSignal) {
            outerSignal.addEventListener('abort', relayAbort, { once: true });
        }

        timeoutHandle = window.setTimeout(() => {
            timeoutController.abort();
        }, Math.max(1000, Number(timeoutMs) || QUERY_TIMEOUT_MS));

        try {
            return await requestFactory(timeoutController.signal);
        } catch (error) {
            if (error?.name === 'AbortError' && !outerSignal?.aborted) {
                throw new Error('Request timed out while waiting for model inference.');
            }
            throw error;
        } finally {
            if (timeoutHandle) {
                window.clearTimeout(timeoutHandle);
            }
            if (outerSignal) {
                outerSignal.removeEventListener('abort', relayAbort);
            }
        }
    }

    async function detectBackendStatusHint() {
        try {
            const response = await tracedFetch(`${API_BASE}/health`, { method: 'GET', cache: 'no-store' }, {
                flow: 'detect-backend-status-hint',
            });
            if (!response.ok) {
                return 'Backend is temporarily unreachable.';
            }

            const payload = await response.json();
            if (payload?.status === 'starting' || payload?.ready === false) {
                return 'Backend warmup is still in progress.';
            }

            if (payload?.status === 'healthy' && payload?.ready === true) {
                return 'Connection dropped while backend is healthy.';
            }
        } catch (_) {
            return 'Backend is temporarily unreachable.';
        }

        return 'Backend is still initializing.';
    }

    function inferDeterministicIntent({ prompt, selectedTemplateId, hasUploadedTemplate }) {
        const normalized = String(prompt || '').toLowerCase();
        const hasTemplateSelected = Boolean(selectedTemplateId || hasUploadedTemplate);
        const hasTemplateAction = /\b(fill|populate|complete|draft|generate|create)\b/.test(normalized);
        const hasTemplateTarget = /\b(template|report|cfir|form)\b/.test(normalized);

        if (hasTemplateAction && hasTemplateTarget) {
            return {
                intent: 'fill_template',
                confidence: hasTemplateSelected ? 0.95 : 0.85,
                reason: hasTemplateSelected
                    ? 'explicit fill intent with selected template'
                    : 'explicit fill intent keywords',
            };
        }

        if (hasTemplateSelected && hasTemplateTarget) {
            return {
                intent: 'fill_template',
                confidence: 0.78,
                reason: 'template target mentioned with selected template',
            };
        }

        return {
            intent: 'query',
            confidence: 0.8,
            reason: hasTemplateSelected
                ? 'selected template ignored because prompt is regular Q&A'
                : 'deterministic query routing',
        };
    }

    async function classifyIntentFallback({ prompt, hasSelectedTemplate, hasUploadedTemplate }) {
        try {
            const response = await tracedFetch(`${API_BASE}/classify-intent`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    prompt,
                    has_selected_template: Boolean(hasSelectedTemplate),
                    has_uploaded_template: Boolean(hasUploadedTemplate),
                }),
            }, {
                flow: 'classify-intent-fallback',
            });

            if (!response.ok) {
                return {
                    intent: 'query',
                    confidence: 0.6,
                    reason: `classifier HTTP ${response.status}`,
                };
            }

            const payload = await response.json();
            const intent = payload?.intent === 'fill_template' ? 'fill_template' : 'query';
            const confidence = Number(payload?.confidence);
            const normalizedConfidence = Number.isFinite(confidence) ? Math.max(0, Math.min(1, confidence)) : 0.6;
            return {
                intent,
                confidence: normalizedConfidence,
                reason: String(payload?.reason || 'classifier fallback'),
            };
        } catch (error) {
            traceState('classify-intent-fallback-error', {
                message: error?.message,
                name: error?.name,
            });
            return {
                intent: 'query',
                confidence: 0.6,
                reason: 'classifier unavailable',
            };
        }
    }

    async function loadTemplateOptions() {
        if (!templateSelect || !backendReady) {
            return;
        }

        const cachedSelection = String(safeGet(TEMPLATE_SELECTION_KEY) || '').trim();
        const previousSelection = String(templateSelect.value || '').trim();
        const preferredSelection = previousSelection || cachedSelection;

        try {
            const response = await tracedFetch(`${API_BASE}/templates`, {
                method: 'GET',
                cache: 'no-store',
            }, {
                flow: 'load-template-options',
            });

            if (!response.ok) {
                throw new Error(`Unable to load templates (${response.status})`);
            }

            const templates = await response.json();
            if (!Array.isArray(templates)) {
                availableTemplates = [];
                return;
            }

            availableTemplates = templates;

            templateSelect.innerHTML = '';

            const emptyOption = document.createElement('option');
            emptyOption.value = '';
            emptyOption.textContent = 'No template selected';
            templateSelect.appendChild(emptyOption);

            let selectedStillExists = false;

            templates.forEach((template) => {
                if (!template || !template.id) {
                    return;
                }

                const option = document.createElement('option');
                option.value = String(template.id);
                const isDefault = Boolean(template.is_default);
                const isActive = template.active !== false;
                const name = String(template.name || template.filename || 'Unnamed template');
                option.textContent = isDefault ? `${name} (default)` : name;
                option.disabled = !isActive;
                templateSelect.appendChild(option);

                if (option.value === preferredSelection) {
                    selectedStillExists = true;
                }
            });

            const nextSelection = selectedStillExists
                ? preferredSelection
                : '';
            templateSelect.value = nextSelection;
            safeSet(TEMPLATE_SELECTION_KEY, nextSelection);

            traceState('load-template-options-success', {
                templateCount: templates.length,
                selectedTemplateId: nextSelection,
            });
        } catch (error) {
            availableTemplates = [];
            traceState('load-template-options-error', {
                message: error?.message,
                name: error?.name,
            });
        }
    }

    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
    }

    function resolveTemplateIdFromPrompt(prompt, templates) {
        const normalizedPrompt = normalizeTemplateLookupText(prompt);
        if (!normalizedPrompt || !Array.isArray(templates) || templates.length === 0) {
            return '';
        }

        let bestTemplateId = '';
        let bestMatchLength = 0;

        templates.forEach((template) => {
            if (!template || !template.id || template.active === false) {
                return;
            }

            const rawFilename = String(template.filename || '');
            const filenameStem = rawFilename.replace(/\.[^.]+$/, '');
            const candidates = [template.name, rawFilename, filenameStem]
                .map((value) => normalizeTemplateLookupText(value))
                .filter((value, index, arr) => value.length >= 3 && arr.indexOf(value) === index);

            candidates.forEach((candidate) => {
                if (!normalizedPrompt.includes(candidate)) {
                    return;
                }
                if (candidate.length > bestMatchLength) {
                    bestMatchLength = candidate.length;
                    bestTemplateId = String(template.id);
                }
            });
        });

        return bestTemplateId;
    }

    function normalizeTemplateLookupText(value) {
        return String(value || '')
            .toLowerCase()
            .replace(/[^a-z0-9\s]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim();
    }

    function restoreDraftState() {
        const draftPrompt = safeGet(PROMPT_DRAFT_KEY);
        if (promptInput && draftPrompt) {
            promptInput.value = draftPrompt;
        }

        const cachedError = safeGet(ERROR_CACHE_KEY);
        if (cachedError) {
            showError(cachedError);
        } else {
            showError('');
        }

        const cachedTemplateName = safeGet(TEMPLATE_FILENAME_KEY);
        if (templateFileInput && cachedTemplateName) {
            templateFileInput.title = `Re-attach template file: ${cachedTemplateName}`;
        }

        updateFeedPlaceholder();
    }

    function startFreshSession() {
        safeSet(PROMPT_DRAFT_KEY, '');
        safeSet(ERROR_CACHE_KEY, '');
        safeSet(TEMPLATE_FILENAME_KEY, '');
        safeSet(TEMPLATE_SELECTION_KEY, '');
        persistActiveConversation(null);
        currentConversationId = null;
        if (templateSelect) {
            templateSelect.value = '';
        }
        renderMessages([], { allowClear: true, reason: 'fresh-session-load' });
    }

    function consumeFreshSessionFlag() {
        try {
            const params = new URLSearchParams(window.location.search || '');
            if (params.get(LOGIN_FRESH_FLAG) !== '1') {
                return false;
            }

            params.delete(LOGIN_FRESH_FLAG);
            const nextQuery = params.toString();
            const nextUrl = `${window.location.pathname}${nextQuery ? `?${nextQuery}` : ''}${window.location.hash || ''}`;
            window.history.replaceState({}, document.title, nextUrl);
            return true;
        } catch (_) {
            return false;
        }
    }

    function ensureStatusContainers() {
        if (!promptForm) {
            return;
        }

        loadingIndicator = null;
        loadingProgressTrack = null;
        loadingProgressFill = null;
        loadingElapsedText = null;

        if (!errorContainer) {
            errorContainer = document.createElement('div');
            errorContainer.id = 'error-container';
            errorContainer.className = 'hidden mt-2 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-400/30 dark:bg-rose-400/10 dark:text-rose-200';
            promptForm.append(errorContainer);
        }
    }

    function safeGet(key) {
        try {
            return localStorage.getItem(key) || '';
        } catch (_) {
            return '';
        }
    }

    function safeSet(key, value) {
        try {
            localStorage.setItem(key, value || '');
        } catch (_) {
            // Best-effort persistence only.
        }
    }

    function readPersistedActiveConversation() {
        const value = safeGet(ACTIVE_CONVERSATION_KEY);
        if (!value) {
            return null;
        }
        const parsed = Number.parseInt(value, 10);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function persistActiveConversation(id) {
        if (id === null || id === undefined) {
            safeSet(ACTIVE_CONVERSATION_KEY, '');
            return;
        }
        safeSet(ACTIVE_CONVERSATION_KEY, String(id));
    }

    function updateFeedPlaceholder() {
        if (!feedPlaceholder) {
            return;
        }
        const hasResults = Boolean(resultContainer?.childElementCount);
        const hasError = errorContainer && !errorContainer.classList.contains('hidden');
        const shouldHide = hasResults || isProcessing || hasError;
        feedPlaceholder.classList.toggle('hidden', shouldHide);
    }

    function formatTimestamp(timestamp) {
        if (!timestamp) {
            return 'Now';
        }

        const parsed = new Date(timestamp);
        if (Number.isNaN(parsed.getTime())) {
            return 'Now';
        }

        return parsed.toLocaleString();
    }

    function escapeHtml(value) {
        return String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
    }

    function initSidebar() {
        if (!sidebar) {
            return;
        }

        let storedPreference = null;
        try {
            storedPreference = localStorage.getItem(SIDEBAR_STORAGE_KEY);
        } catch (_) {
            storedPreference = null;
        }

        const initialCollapsed = storedPreference !== null ? storedPreference === 'true' : collapseQuery.matches;
        setSidebarState(initialCollapsed, storedPreference !== null);

        sidebarToggleButtons.forEach((button) => {
            button.addEventListener('click', () => {
                const isCollapsed = sidebar.getAttribute('data-collapsed') === 'true';
                setSidebarState(!isCollapsed, true);
            });
        });

        sidebarBrands.forEach((brand) => {
            brand.addEventListener('click', (event) => {
                if (sidebar.getAttribute('data-collapsed') === 'true') {
                    event.preventDefault();
                    setSidebarState(false, true);
                }
            });
        });

        const handleQueryChange = (event) => {
            if (userPreferenceLocked) {
                return;
            }
            setSidebarState(event.matches, false);
        };

        if (typeof collapseQuery.addEventListener === 'function') {
            collapseQuery.addEventListener('change', handleQueryChange);
        } else if (typeof collapseQuery.addListener === 'function') {
            collapseQuery.addListener(handleQueryChange);
        }
    }

    function setSidebarState(shouldCollapse, persistPreference) {
        sidebar?.setAttribute('data-collapsed', shouldCollapse ? 'true' : 'false');
        sidebarToggleButtons.forEach((button) => {
            button.setAttribute('aria-pressed', shouldCollapse ? 'true' : 'false');
        });
        updateSidebarToggleVisuals(shouldCollapse);

        if (!shouldCollapse) {
            hideSidebarTooltip();
        }

        if (!persistPreference) {
            return;
        }

        userPreferenceLocked = true;

        try {
            localStorage.setItem(SIDEBAR_STORAGE_KEY, String(shouldCollapse));
        } catch (_) {
            // Best-effort persistence only.
        }
    }

    function updateSidebarToggleVisuals(isCollapsed) {
        const label = isCollapsed ? 'Expand sidebar' : 'Collapse sidebar';
        const direction = isCollapsed ? 'expand' : 'collapse';
        sidebarToggleButtons.forEach((button) => {
            button.setAttribute('aria-label', label);
            button.setAttribute('data-direction', direction);
        });
    }

    function initSidebarTooltips() {
        if (!sidebar || sidebarLinks.length === 0) {
            return;
        }

        const tooltip = document.createElement('div');
        tooltip.className = 'sidebar-tooltip';
        tooltip.style.opacity = '0';
        tooltip.style.visibility = 'hidden';
        document.body.appendChild(tooltip);

        let tooltipVisible = false;

        const showTooltip = (text, event) => {
            if (!isSidebarCollapsed()) {
                return;
            }
            tooltip.textContent = text;
            tooltipVisible = true;
            tooltip.style.visibility = 'visible';
            tooltip.style.opacity = '1';
            positionTooltip(event);
        };

        const hideTooltip = () => {
            tooltipVisible = false;
            tooltip.style.opacity = '0';
            tooltip.style.visibility = 'hidden';
        };

        const positionTooltip = (event) => {
            if (!tooltipVisible) {
                return;
            }
            const offsetX = 18;
            tooltip.style.left = `${event.clientX + offsetX}px`;
            tooltip.style.top = `${event.clientY}px`;
        };

        const isSidebarCollapsed = () => sidebar?.getAttribute('data-collapsed') === 'true';

        sidebarLinks.forEach((link) => {
            const label = link.querySelector('.sidebar-label')?.textContent?.trim();
            if (!label) {
                return;
            }
            link.setAttribute('aria-label', label);

            link.addEventListener('mouseenter', (event) => {
                showTooltip(label, event);
            });

            link.addEventListener('mousemove', (event) => {
                if (!tooltipVisible) {
                    return;
                }
                positionTooltip(event);
            });

            link.addEventListener('mouseleave', hideTooltip);
        });

        hideSidebarTooltip = hideTooltip;
    }
});
