const API_BASE = 'http://localhost:8000/api';

document.addEventListener('DOMContentLoaded', () => {
    const promptForm = document.getElementById('prompt-form');
    const promptInput = document.getElementById('prompt-input');
    const resultContainer = document.getElementById('result-container');
    const loadingIndicator = document.getElementById('loading-indicator');
    const errorContainer = document.getElementById('error-container');
    const submitButton = document.getElementById('submit-button');
    const fillTemplateButton = document.getElementById('fill-template-btn');
    const templateFileInput = document.getElementById('template-file');
    const submitLabel = submitButton?.querySelector('[data-role="label"]');
    const submitSpinner = submitButton?.querySelector('[data-role="spinner"]');
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

    if (promptForm && promptInput) {
        promptForm.addEventListener('submit', async (event) => {
            event.preventDefault();
            if (isProcessing) {
                cancelActiveRequest();
                return;
            }

            const prompt = promptInput.value.trim();

            if (!prompt) {
                showError('Please describe the fraud scenario you want to generate.');
                return;
            }

            clearResult();
            showError('');
            showLoading(true);

            const controller = new AbortController();
            activeController = controller;

            try {
                const response = await fetch(`${API_BASE}/query`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        prompt,
                        temperature: 0.2,
                    }),
                    signal: controller.signal,
                });

                if (!response.ok) {
                    throw new Error(`API error: ${response.status}`);
                }

                const data = await response.json();
                displayResult(data);
            } catch (error) {
                if (error.name === 'AbortError') {
                    showError('Prompt cancelled.');
                } else {
                    showError(error.message || 'Unable to reach the CFIR API.');
                }
            } finally {
                showLoading(false);
                if (activeController === controller) {
                    activeController = null;
                }
            }
        });
    }

    if (fillTemplateButton && templateFileInput && promptInput) {
        fillTemplateButton.addEventListener('click', async () => {
            if (isProcessing) {
                return;
            }

            if (!templateFileInput.files || !templateFileInput.files.length) {
                showError('Please select a template file.');
                return;
            }

            const file = templateFileInput.files[0];
            const prompt = promptInput.value.trim();
            if (!prompt) {
                showError('Please enter a prompt describing what to fill.');
                return;
            }

            showError('');
            showLoading(true);
            try {
                const formData = new FormData();
                formData.append('file', file);
                formData.append('prompt', prompt);

                const response = await fetch(`${API_BASE}/fill-template`, {
                    method: 'POST',
                    body: formData,
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
            } catch (error) {
                showError(error.message || 'Template filling failed');
            } finally {
                showLoading(false);
            }
        });
    }

    function cancelActiveRequest() {
        if (!activeController) {
            return;
        }
        activeController.abort();
    }

    function showLoading(isLoading) {
        if (loadingIndicator) {
            loadingIndicator.classList.toggle('hidden', !isLoading);
        }
        toggleSubmitState(isLoading);
        updateFeedPlaceholder();
    }

    function toggleSubmitState(isLoading) {
        isProcessing = isLoading;
        if (!submitButton) return;
        submitButton.setAttribute('data-state', isLoading ? 'cancel' : 'idle');
        submitButton.setAttribute('aria-busy', String(isLoading));
        submitButton.disabled = false;
        if (submitSpinner) {
            submitSpinner.classList.toggle('hidden', !isLoading);
        }
        if (submitLabel) {
            submitLabel.textContent = isLoading ? 'Cancel' : 'Send prompt';
        }
    }

    function showError(message) {
        if (!errorContainer) return;
        if (!message) {
            errorContainer.textContent = '';
            errorContainer.classList.add('hidden');
            updateFeedPlaceholder();
            return;
        }

        errorContainer.textContent = message;
        errorContainer.classList.remove('hidden');
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
        updateFeedPlaceholder();
    }

    function clearResult() {
        if (!resultContainer) return;
        resultContainer.innerHTML = '';
        updateFeedPlaceholder();
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