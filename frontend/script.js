const API_BASE = 'http://localhost:8000/api';

document.addEventListener('DOMContentLoaded', () => {
    const promptForm = document.getElementById('prompt-form');
    const promptInput = document.getElementById('prompt-input');
    const resultContainer = document.getElementById('result-container');
    const loadingIndicator = document.getElementById('loading-indicator');
    const errorContainer = document.getElementById('error-container');

    if (!promptForm || !promptInput) {
        return;
    }

    promptForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const prompt = promptInput.value.trim();

        if (!prompt) {
            showError('Please describe the fraud scenario you want to generate.');
            return;
        }

        clearResult();
        showError('');
        showLoading(true);

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
            });

            if (!response.ok) {
                throw new Error(`API error: ${response.status}`);
            }

            const data = await response.json();
            displayResult(data);
        } catch (error) {
            showError(error.message || 'Unable to reach the CFIR API.');
        } finally {
            showLoading(false);
        }
    });

    function showLoading(isLoading) {
        if (!loadingIndicator) return;
        loadingIndicator.classList.toggle('hidden', !isLoading);
    }

    function showError(message) {
        if (!errorContainer) return;
        if (!message) {
            errorContainer.textContent = '';
            errorContainer.classList.add('hidden');
            return;
        }

        errorContainer.textContent = message;
        errorContainer.classList.remove('hidden');
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
    }

    function clearResult() {
        if (!resultContainer) return;
        resultContainer.innerHTML = '';
    }
});