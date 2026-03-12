const API_BASE = 'http://localhost:8000/api';
const PROMPT_DRAFT_KEY = 'landing-prompt-draft';
const RESULT_CACHE_KEY = 'landing-result-html';
const ERROR_CACHE_KEY = 'landing-error-message';
const TEMPLATE_FILENAME_KEY = 'landing-template-filename';

document.addEventListener('DOMContentLoaded', () => {
    const promptForm = document.getElementById('prompt-form');
    const promptInput = document.getElementById('prompt-input');
    const resultContainer = document.getElementById('result-container');
    let loadingIndicator = document.getElementById('loading-indicator');
    let errorContainer = document.getElementById('error-container');
    const submitButton = document.getElementById('submit-button');
    const fillTemplateButton = document.getElementById('fill-template-btn');
    const templateFileInput = document.getElementById('template-file');
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

    setBackendReady(false, 'Checking backend status...');

    ensureStatusContainers();
    restoreDraftState();
    void pollBackendHealth();

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
                clearResult();
                displayResult(data);
            } catch (error) {
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
            try {
                const response = await fetchWithStartupRetry(() => {
                    const formData = new FormData();
                    formData.append('file', file);
                    formData.append('prompt', prompt);
                    return fetch(`${API_BASE}/fill-template`, {
                        method: 'POST',
                        body: formData,
                    });
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(errorText || 'Template filling failed');
                }

                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `filled_${file.name}`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                window.URL.revokeObjectURL(url);
                showError('');
            } catch (error) {
                showError(error.message || 'Template filling failed');
            } finally {
                showLoading(false, 'template');
                activeOperation = null;
            }
        });
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
                        return;
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
    }

    function showError(message) {
        if (!errorContainer) return;
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

    function displayResult(data) {
        if (!resultContainer || !data) {
            return;
        }

        const fragments = [];

        const answerCard = document.createElement('div');
        answerCard.className = 'rounded-2xl border border-slate-200 dark:border-white/10 bg-white/80 dark:bg-white/5 px-5 py-4';

        const answerLabel = document.createElement('div');
        answerLabel.className = 'text-sm uppercase tracking-[0.35em] text-slate-400 mb-3';
        answerLabel.textContent = 'Generated CFIR';

        const answerBody = document.createElement('div');
        answerBody.className = 'prose prose-slate dark:prose-invert whitespace-pre-wrap text-sm sm:text-base';
        answerBody.textContent = data.answer;

        answerCard.append(answerLabel, answerBody);
        fragments.push(answerCard);

        if (Array.isArray(data.sources) && data.sources.length > 0) {
            const sourcesWrapper = document.createElement('div');
            sourcesWrapper.className = 'rounded-2xl border border-slate-200 dark:border-white/10 px-5 py-4 space-y-3';
            const heading = document.createElement('p');
            heading.className = 'text-sm font-semibold text-slate-700 dark:text-slate-100';
            heading.textContent = 'Evidence excerpts';
            sourcesWrapper.appendChild(heading);

            data.sources.forEach((source) => {
                const item = document.createElement('div');
                item.className = 'rounded-xl bg-slate-50 dark:bg-white/5 px-4 py-3 text-sm text-slate-600 dark:text-slate-200';

                const title = document.createElement('div');
                title.className = 'font-medium text-brand-foam mb-1';
                title.textContent = `📁 ${source.filename}`;
                item.appendChild(title);

                const snippet = document.createElement('p');
                snippet.className = 'text-slate-500 dark:text-slate-300';
                snippet.textContent = source.snippet;
                item.appendChild(snippet);

                if (source.relevance_score) {
                    const score = document.createElement('p');
                    score.className = 'text-xs text-slate-400 mt-1';
                    score.textContent = `Relevance: ${(source.relevance_score * 100).toFixed(1)}%`;
                    item.appendChild(score);
                }

                sourcesWrapper.appendChild(item);
            });

            fragments.push(sourcesWrapper);
        }

        if (data.processing_time_ms) {
            const meta = document.createElement('div');
            meta.className = 'text-xs text-right text-slate-400';
            meta.textContent = `Processed in ${data.processing_time_ms} ms`;
            fragments.push(meta);
        }

        resultContainer.append(...fragments);
        resultContainer.scrollTop = resultContainer.scrollHeight;
        safeSet(RESULT_CACHE_KEY, resultContainer.innerHTML);
        updateFeedPlaceholder();
    }

    function clearResult() {
        if (!resultContainer) return;
        resultContainer.innerHTML = '';
        safeSet(RESULT_CACHE_KEY, '');
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

        const cachedResult = safeGet(RESULT_CACHE_KEY);
        if (resultContainer && cachedResult) {
            resultContainer.innerHTML = cachedResult;
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

    function updateFeedPlaceholder() {
        if (!feedPlaceholder) {
            return;
        }
        const hasResults = Boolean(resultContainer?.childElementCount);
        const hasError = errorContainer && !errorContainer.classList.contains('hidden');
        const shouldHide = hasResults || isProcessing || hasError;
        feedPlaceholder.classList.toggle('hidden', shouldHide);
    }

    initSidebar();
    initSidebarTooltips();

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