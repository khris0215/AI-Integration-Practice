const API_BASE = 'http://localhost:8000/api';
const PROMPT_DRAFT_KEY = 'landing-prompt-draft';
const ERROR_CACHE_KEY = 'landing-error-message';
const TEMPLATE_FILENAME_KEY = 'landing-template-filename';
const ACTIVE_CONVERSATION_KEY = 'landing-active-conversation-id';

document.addEventListener('DOMContentLoaded', () => {
    const promptForm = document.getElementById('prompt-form');
    const promptInput = document.getElementById('prompt-input');
    const resultContainer = document.getElementById('result-container');
    let loadingIndicator = document.getElementById('loading-indicator');
    let errorContainer = document.getElementById('error-container');
    const submitButton = document.getElementById('submit-button');
    const fillTemplateButton = document.getElementById('fill-template-btn');
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

    let userPreferenceLocked = false;
    let hideSidebarTooltip = () => {};
    let activeController = null;
    let isProcessing = false;
    let activeOperation = null;
    let backendReady = false;
    let currentConversationId = null;
    let conversations = [];
    let transientMessageId = 0;

    if (promptForm) {
        // Guard against accidental page reload if later init code throws.
        promptForm.addEventListener('submit', (event) => {
            event.preventDefault();
        }, { capture: true });
    }

    setBackendReady(false, 'Checking backend status...');

    ensureStatusContainers();
    restoreDraftState();
    try {
        initSidebar();
        initSidebarTooltips();
    } catch (error) {
        console.warn('Sidebar init skipped due to runtime error:', error);
    }

    void initializeApp();

    async function initializeApp() {
        const isHealthy = await pollBackendHealth();
        if (!isHealthy) {
            return;
        }
        await refreshConversations();
    }

    if (promptInput) {
        promptInput.addEventListener('input', () => {
            safeSet(PROMPT_DRAFT_KEY, promptInput.value);
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

    if (newChatButton) {
        newChatButton.addEventListener('click', async () => {
            if (!backendReady || activeOperation) {
                return;
            }
            currentConversationId = null;
            persistActiveConversation(null);
            renderMessages([]);
            renderConversationList();
            showError('');
            promptInput?.focus();
        });
    }

    if (promptForm && promptInput) {
        promptForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (activeOperation === 'query') {
                cancelActiveRequest();
                return;
            }
            if (activeOperation && activeOperation !== 'query') {
                showError('Template filling is in progress. Please wait for it to finish.');
                return;
            }

            const prompt = promptInput.value.trim();

            if (!backendReady) {
                showError('Backend is still starting. Please wait until status shows connected.');
                return;
            }

            if (!prompt) {
                showError('Please describe the fraud scenario you want to generate.');
                return;
            }

            showError('');
            activeOperation = 'query';
            showLoading(true, 'query');
            safeSet(PROMPT_DRAFT_KEY, prompt);
            appendMessageToFeed({ role: 'user', content: prompt, timestamp: new Date().toISOString() });
            const pendingAssistantId = appendTypingMessage('AI is thinking...');

            const controller = new AbortController();
            activeController = controller;

            try {
                const response = await fetchWithStartupRetry(
                    () => fetch(`${API_BASE}/query`, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            prompt,
                            conversation_id: currentConversationId,
                            temperature: 0.2,
                        }),
                        signal: controller.signal,
                    }),
                    controller.signal,
                );

                if (!response.ok) {
                    throw new Error(`API error: ${response.status}`);
                }

                const data = await response.json();
                if (typeof data.conversation_id === 'number') {
                    currentConversationId = data.conversation_id;
                    persistActiveConversation(currentConversationId);
                }

                removeTransientMessage(pendingAssistantId);
                appendMessageToFeed({
                    role: 'assistant',
                    content: data.answer || 'Response received.',
                    timestamp: new Date().toISOString(),
                });

                await refreshConversations(currentConversationId);
                promptInput.value = '';
                safeSet(PROMPT_DRAFT_KEY, '');
            } catch (error) {
                removeTransientMessage(pendingAssistantId);
                if (error.name === 'AbortError') {
                    showError('Prompt cancelled.');
                } else {
                    showError(error.message || 'Unable to reach the CFIR API.');
                }
            } finally {
                showLoading(false, 'query');
                if (activeController === controller) {
                    activeController = null;
                }
                activeOperation = null;
            }
        });
    }

    if (fillTemplateButton && templateFileInput && promptInput) {
        fillTemplateButton.addEventListener('click', async () => {
            if (activeOperation) {
                if (activeOperation === 'query') {
                    showError('A prompt request is running. Wait for it to finish or cancel it first.');
                }
                return;
            }

            if (!templateFileInput.files || !templateFileInput.files.length) {
                showError('Please select a template file.');
                return;
            }

            const file = templateFileInput.files[0];
            const prompt = promptInput.value.trim();
            if (!backendReady) {
                showError('Backend is still starting. Please wait until status shows connected.');
                return;
            }
            if (!prompt) {
                showError('Please enter a prompt describing what to fill.');
                return;
            }

            showError('');
            activeOperation = 'template';
            showLoading(true, 'template');
            const userTemplateMessage = `Fill template request (${file.name}):\n${prompt}`;
            appendMessageToFeed({ role: 'user', content: userTemplateMessage, timestamp: new Date().toISOString() });
            const pendingTemplateId = appendTypingMessage('AI is filling the template...');
            try {
                const response = await fetchWithStartupRetry(() => {
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('prompt', prompt);
                    if (Number.isFinite(currentConversationId)) {
                        formData.append('conversation_id', String(currentConversationId));
                    }
                    return fetch(`${API_BASE}/fill-template`, {
                        method: 'POST',
                        body: formData,
                    });
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(errorText || 'Template filling failed.');
                }

                const data = await response.json();
                if (typeof data.conversation_id === 'number') {
                    currentConversationId = data.conversation_id;
                    persistActiveConversation(currentConversationId);
                }

                removeTransientMessage(pendingTemplateId);
                appendMessageToFeed({
                    role: 'assistant',
                    content: data.answer || 'Template ready. Download from the attachment below.',
                    timestamp: new Date().toISOString(),
                    attachment_id: data.attachment_id,
                    attachment_filename: data.attachment_filename,
                });

                templateFileInput.value = '';
                safeSet(TEMPLATE_FILENAME_KEY, '');
                showError('');
                await refreshConversations(currentConversationId);
            } catch (error) {
                removeTransientMessage(pendingTemplateId);
                showError(error.message || 'Template filling failed');
            } finally {
                showLoading(false, 'template');
                activeOperation = null;
            }
        });
    }

    async function refreshConversations(preferredId = null) {
        try {
            const response = await fetch(`${API_BASE}/conversations`, {
                method: 'GET',
                cache: 'no-store',
            });
            if (!response.ok) {
                throw new Error(`Unable to load conversations (${response.status})`);
            }

            conversations = await response.json();
            const restoredId = readPersistedActiveConversation();
            const targetId = preferredId ?? currentConversationId ?? restoredId;
            const hasTarget = conversations.some((conv) => conv.id === targetId);

            if (hasTarget) {
                currentConversationId = targetId;
            } else if (conversations.length > 0) {
                currentConversationId = conversations[0].id;
            } else {
                currentConversationId = null;
            }

            persistActiveConversation(currentConversationId);
            renderConversationList();

            if (currentConversationId !== null) {
                await loadConversationMessages(currentConversationId);
            } else {
                renderMessages([]);
            }
        } catch (error) {
            showError(error.message || 'Failed to load conversation history.');
        }
    }

    async function loadConversationMessages(conversationId) {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
            method: 'GET',
            cache: 'no-store',
        });

        if (!response.ok) {
            throw new Error(`Unable to load messages (${response.status})`);
        }

        const messages = await response.json();
        renderMessages(messages);
    }

    async function removeConversation(conversationId) {
        const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
            method: 'DELETE',
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

    function renderMessages(messages) {
        if (!resultContainer) {
            return;
        }

        resultContainer.innerHTML = '';

        if (!Array.isArray(messages) || messages.length === 0) {
            updateFeedPlaceholder();
            return;
        }

        messages.forEach((message) => {
            appendMessageToFeed(message);
        });

        resultContainer.scrollTop = resultContainer.scrollHeight;
        updateFeedPlaceholder();
    }

    function appendMessageToFeed(message) {
        if (!resultContainer) {
            return null;
        }

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
        if (loadingIndicator) {
            loadingIndicator.classList.toggle('hidden', !isLoading);
        }
        toggleSubmitState(isLoading, operation);
        updateFeedPlaceholder();
    }

    function toggleSubmitState(isLoading, operation) {
        isProcessing = isLoading;
        if (submitButton) {
            const isQueryOperation = operation === 'query';
            submitButton.setAttribute('data-state', isLoading && isQueryOperation ? 'cancel' : 'idle');
            submitButton.setAttribute('aria-busy', String(isLoading && isQueryOperation));
            submitButton.disabled = !backendReady || Boolean(isLoading && !isQueryOperation);
            if (submitSpinner) {
                submitSpinner.classList.toggle('hidden', !(isLoading && isQueryOperation));
            }
            if (submitLabel) {
                submitLabel.textContent = isLoading && isQueryOperation ? 'Cancel' : 'Send prompt';
            }
        }

        if (fillTemplateButton) {
            fillTemplateButton.disabled = !backendReady || isLoading;
            fillTemplateButton.classList.toggle('opacity-60', isLoading);
            fillTemplateButton.classList.toggle('cursor-not-allowed', isLoading);
        }

        if (templateFileInput) {
            templateFileInput.disabled = !backendReady || isLoading;
        }

        if (newChatButton) {
            newChatButton.disabled = !backendReady || isLoading;
        }
    }

    async function pollBackendHealth() {
        const maxAttempts = 15;
        const intervalMs = 2000;

        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            try {
                setBackendReady(false, `Connecting to backend... (${attempt}/${maxAttempts})`);
                const response = await fetch(`${API_BASE}/health`, {
                    method: 'GET',
                    cache: 'no-store',
                });
                if (response.ok) {
                    const payload = await response.json();
                    if (payload && payload.status === 'healthy' && payload.ready === true) {
                        setBackendReady(true, `Backend connected (v${payload.version || 'unknown'}).`);
                        return true;
                    }

                    if (payload && payload.status === 'starting') {
                        const startupError = payload.startup_error ? ` Warmup issue: ${payload.startup_error}` : '';
                        setBackendReady(false, `Backend online, finishing warmup...${startupError}`);
                    }
                }
            } catch (_) {
                // Ignore transient failures during startup and continue polling.
            }

            if (attempt < maxAttempts) {
                await wait(intervalMs);
            }
        }

        setBackendReady(false, 'Backend unavailable. Start the API server and refresh this page.');
        showError('Backend health check failed after multiple attempts.');
        return false;
    }

    function setBackendReady(isReady, message) {
        backendReady = isReady;
        if (backendStatus) {
            backendStatus.textContent = message;
            backendStatus.classList.toggle('hidden', isReady);
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
        if (fillTemplateButton) {
            fillTemplateButton.disabled = !isReady;
            fillTemplateButton.classList.toggle('opacity-60', !isReady);
            fillTemplateButton.classList.toggle('cursor-not-allowed', !isReady);
        }
        if (templateFileInput) {
            templateFileInput.disabled = !isReady;
        }
        if (newChatButton) {
            newChatButton.disabled = !isReady;
        }
    }

    function showError(message) {
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
        safeSet(ERROR_CACHE_KEY, message);
        updateFeedPlaceholder();
    }

    async function fetchWithStartupRetry(requestFactory, signal, maxAttempts = 4) {
        let lastError = null;
        for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
            if (signal?.aborted) {
                throw new DOMException('The operation was aborted.', 'AbortError');
            }

            try {
                const response = await requestFactory();
                if (response.ok) {
                    return response;
                }

                if ([502, 503, 504].includes(response.status) && attempt < maxAttempts) {
                    showError(`Backend is waking up... retry ${attempt}/${maxAttempts - 1}`);
                    await wait(900 * attempt);
                    continue;
                }

                return response;
            } catch (error) {
                if (error?.name === 'AbortError') {
                    throw error;
                }

                lastError = error;
                if (attempt < maxAttempts) {
                    const statusHint = await detectBackendStatusHint();
                    showError(`${statusHint} Retrying... (${attempt}/${maxAttempts - 1})`);
                    await wait(900 * attempt);
                    continue;
                }
            }
        }

        const fallbackMessage = 'Backend is still starting. Your draft is preserved - try again in a few seconds.';
        throw new Error(lastError?.message || fallbackMessage);
    }

    async function detectBackendStatusHint() {
        try {
            const response = await fetch(`${API_BASE}/health`, { method: 'GET', cache: 'no-store' });
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

    function wait(ms) {
        return new Promise((resolve) => {
            window.setTimeout(resolve, ms);
        });
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

    function ensureStatusContainers() {
        if (!promptForm) {
            return;
        }

        if (!loadingIndicator) {
            loadingIndicator = document.createElement('div');
            loadingIndicator.id = 'loading-indicator';
            loadingIndicator.className = 'hidden mt-2 text-sm text-slate-500 dark:text-slate-300';
            loadingIndicator.textContent = 'Processing request...';
            promptForm.prepend(loadingIndicator);
        }

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
