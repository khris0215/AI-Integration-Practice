const API_BASE = 'http://localhost:8000';  // FastAPI backend URL

document.getElementById('generateBtn').addEventListener('click', async () => {
    const prompt = document.getElementById('prompt').value.trim();
    if (!prompt) {
        showError('Please enter a query.');
        return;
    }

    showLoading(true);
    hideResult();
    hideError();

    try {
        const response = await fetch(`${API_BASE}/query`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt })
        });

        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Error ${response.status}: ${errorText}`);
        }

        const data = await response.json();
        displayResult(data);
    } catch (err) {
        showError(err.message);
    } finally {
        showLoading(false);
    }
});

document.getElementById('ingestBtn').addEventListener('click', async () => {
    showLoading(true);
    hideResult();
    hideError();

    try {
        const response = await fetch(`${API_BASE}/ingest`, { method: 'POST' });
        if (!response.ok) {
            const errorText = await response.text();
            throw new Error(`Error ${response.status}: ${errorText}`);
        }
        alert('Ingestion started. Check backend logs for progress.');
    } catch (err) {
        showError(err.message);
    } finally {
        showLoading(false);
    }
});

function displayResult(data) {
    document.getElementById('answer').textContent = data.answer;

    const sourcesDiv = document.getElementById('sources');
    sourcesDiv.innerHTML = '';
    data.sources.forEach(src => {
        const card = document.createElement('div');
        card.className = 'bg-gray-50 p-3 rounded border border-gray-200';
        card.innerHTML = `
            <div class="font-medium text-blue-600">📁 ${src.filename}</div>
            <div class="text-sm text-gray-600 mt-1">${src.snippet}</div>
        `;
        sourcesDiv.appendChild(card);
    });

    document.getElementById('result').classList.remove('hidden');
}

function showLoading(show) {
    document.getElementById('loading').classList.toggle('hidden', !show);
}

function showError(message) {
    const errorDiv = document.getElementById('error');
    errorDiv.textContent = message;
    errorDiv.classList.remove('hidden');
}

function hideResult() {
    document.getElementById('result').classList.add('hidden');
}

function hideError() {
    document.getElementById('error').classList.add('hidden');
}