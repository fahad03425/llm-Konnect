import { useEffect, useState } from 'react';
import { Download, Trash2, CheckCircle2, AlertCircle, RotateCcw, Loader2 } from 'lucide-react';

type ModelState = {
    models: string[];
    active_model: string;
    ollama?: { available?: boolean; error?: string | null; resolved_model?: string | null };
    hardware?: { recommended_profile?: string; recommended_models?: string[] };
};

type DownloadProgress = {
    model: string;
    status: string;
    completed: number;
    total: number;
    percent: number;
};

function formatBytes(bytes: number): string {
    if (!bytes || bytes <= 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

const POPULAR_OLLAMA_MODELS: { category: string; models: string[] }[] = [
    {
        category: 'Fast & Lightweight (CPU / Low VRAM)',
        models: [
            'qwen2.5:0.5b',
            'qwen2.5:1.5b',
            'qwen2.5:3b',
            'llama3.2:1b',
            'llama3.2:3b',
            'phi4-mini',
            'phi3:mini',
            'gemma2:2b',
            'gemma3:1b',
            'tinyllama',
            'smollm2:1.7b',
        ],
    },
    {
        category: 'Standard & High Quality (GPU recommended)',
        models: [
            'qwen2.5:7b',
            'qwen2.5:14b',
            'llama3.1:8b',
            'llama3:8b',
            'mistral:7b',
            'gemma2:9b',
            'gemma3:4b',
            'phi4:14b',
        ],
    },
    {
        category: 'Reasoning & DeepSeek',
        models: [
            'deepseek-r1:1.5b',
            'deepseek-r1:7b',
            'deepseek-r1:8b',
            'deepseek-coder:6.7b',
            'deepseek-r1:14b',
        ],
    },
    {
        category: 'Coding Specialists',
        models: [
            'qwen2.5-coder:1.5b',
            'qwen2.5-coder:7b',
            'starcoder2:3b',
            'codellama:7b',
        ],
    },
];

async function readError(response: Response): Promise<string> {
    const body = await response.json().catch(() => null);
    return body?.detail || `Request failed (${response.status})`;
}

export default function ModelSettings() {
    const [state, setState] = useState<ModelState | null>(null);
    const [selected, setSelected] = useState('');
    const [selectedDownloadModel, setSelectedDownloadModel] = useState('qwen2.5:1.5b');
    const [customModelName, setCustomModelName] = useState('');
    const [isCustomMode, setIsCustomMode] = useState(false);
    const [busy, setBusy] = useState(false);
    const [isRefreshing, setIsRefreshing] = useState(false);
    const [downloadProgress, setDownloadProgress] = useState<DownloadProgress | null>(null);
    const [deletingModel, setDeletingModel] = useState<string | null>(null);
    const [status, setStatus] = useState('');
    const [error, setError] = useState('');

    const refresh = async () => {
        try {
            const response = await fetch('/api/models');
            if (!response.ok) throw new Error(await readError(response));
            const next: ModelState = await response.json();
            setState(next);
            setSelected(current => current && next.models.includes(current) ? current : next.active_model);
            window.dispatchEvent(new Event('models:updated'));
            if (next.ollama?.available === false) {
                setError('Ollama is offline. Start Ollama to use local AI models.');
            } else {
                setError('');
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        }
    };

    const handleManualRefresh = async () => {
        setIsRefreshing(true);
        try {
            await refresh();
        } finally {
            setIsRefreshing(false);
        }
    };

    useEffect(() => {
        refresh();
    }, []);

    const action = async (path: string, body: object, success: string) => {
        setBusy(true);
        setError('');
        setStatus('');
        try {
            const response = await fetch(`/api/models/${path}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (!response.ok) throw new Error(await readError(response));
            await refresh();
            setStatus(success);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setBusy(false);
        }
    };

    const deleteModel = async (modelName: string) => {
        if (!window.confirm(`Are you sure you want to delete model "${modelName}" from Ollama storage?`)) {
            return;
        }
        setDeletingModel(modelName);
        setBusy(true);
        setError('');
        setStatus(`Deleting ${modelName}…`);
        try {
            const response = await fetch(`/api/models/${encodeURIComponent(modelName)}`, {
                method: 'DELETE',
            });
            if (!response.ok) throw new Error(await readError(response));
            await refresh();
            setStatus(`Model ${modelName} deleted successfully`);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setDeletingModel(null);
            setBusy(false);
        }
    };

    const pull = async () => {
        const model = isCustomMode ? customModelName.trim() : selectedDownloadModel.trim();
        if (!model) return;
        setBusy(true);
        setError('');
        setStatus(`Starting download for ${model}…`);
        setDownloadProgress({
            model,
            status: 'Connecting to Ollama…',
            completed: 0,
            total: 0,
            percent: 0,
        });

        try {
            const response = await fetch('/api/models/pull', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model, stream: true }),
            });
            if (!response.ok) throw new Error(await readError(response));
            if (!response.body) throw new Error('Download progress is unavailable');

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let pending = '';
            let failure = '';
            let complete = false;

            const consume = (line: string) => {
                if (!line.trim()) return;
                try {
                    const update = JSON.parse(line);
                    if (String(update.status).startsWith('error:')) failure = update.status;
                    if (update.status === 'success') complete = true;

                    const completedBytes = typeof update.completed === 'number' ? update.completed : 0;
                    const totalBytes = typeof update.total === 'number' ? update.total : 0;
                    const percent = typeof update.percent === 'number'
                        ? update.percent
                        : (totalBytes > 0 ? Math.round((completedBytes / totalBytes) * 100) : 0);

                    setDownloadProgress({
                        model,
                        status: update.status || 'Downloading…',
                        completed: completedBytes,
                        total: totalBytes,
                        percent: Math.min(100, Math.max(0, percent)),
                    });

                    if (update.status === 'success') {
                        setStatus(`✓ ${model} downloaded successfully`);
                    } else if (totalBytes > 0) {
                        setStatus(`Downloading ${model}: ${formatBytes(completedBytes)} / ${formatBytes(totalBytes)} (${percent}%)`);
                    } else {
                        setStatus(`${update.status || 'Downloading'} ${model}…`);
                    }
                } catch {
                    // non-json line ignore
                }
            };

            while (true) {
                const { value, done } = await reader.read();
                pending += decoder.decode(value || new Uint8Array(), { stream: !done });
                const lines = pending.split('\n');
                pending = lines.pop() || '';
                for (const line of lines) {
                    consume(line);
                }
                if (done) break;
            }
            consume(pending);

            if (failure) throw new Error(failure);
            if (!complete) throw new Error('Download ended before completion. Retry the download.');

            await refresh();
            setSelected(model);
            if (isCustomMode) {
                setCustomModelName('');
            }
            setStatus(`✓ ${model} downloaded and ready to use`);
            setTimeout(() => {
                setDownloadProgress(null);
            }, 3500);
        } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
            setDownloadProgress(null);
        } finally {
            setBusy(false);
        }
    };

    const targetDownloadModel = isCustomMode ? customModelName.trim() : selectedDownloadModel.trim();
    const isTargetAlreadyDownloaded = (state?.models || []).includes(targetDownloadModel);

    return (
        <div className="settings-section">
            <div className="settings-section-header">
                <span className="settings-section-title">Local AI model</span>
                <div className="settings-header-actions">
                    <button
                        type="button"
                        className="settings-refresh-btn"
                        onClick={handleManualRefresh}
                        disabled={busy || isRefreshing}
                        title="Refresh installed models and connection status"
                        aria-label="Refresh models"
                    >
                        <RotateCcw size={13} className={isRefreshing ? 'spin' : ''} />
                        <span>{isRefreshing ? 'Refreshing…' : 'Refresh'}</span>
                    </button>
                    <span className="settings-section-badge">Ollama</span>
                </div>
            </div>

            <p className="settings-model-note">
                Active model: <strong>{state?.active_model || 'Loading…'}</strong>.
                {state?.ollama?.resolved_model && state.ollama.resolved_model !== state.active_model
                    ? ` Available fallback: ${state.ollama.resolved_model}.`
                    : ''}{' '}
                Roman Urdu chat uses <code>llama3.2:3b</code> when available.
            </p>

            {/* Active Model Selection & Quick Load/Unload */}
            <div className="settings-model-control-box">
                <div className="settings-model-row">
                    <select
                        className="settings-input"
                        aria-label="Installed model"
                        value={selected}
                        onChange={event => setSelected(event.target.value)}
                        disabled={busy || !state || (state?.models || []).length === 0}
                    >
                        {(state?.models || []).length === 0 ? (
                            <option value="">No models downloaded yet</option>
                        ) : (
                            (state?.models || []).map(model => (
                                <option key={model} value={model}>
                                    {model} {model === state?.active_model ? '(Active)' : ''}
                                </option>
                            ))
                        )}
                    </select>
                    <button
                        className="settings-model-action"
                        type="button"
                        disabled={busy || !selected || selected === state?.active_model}
                        onClick={() => action('select', { model: selected }, `${selected} selected as active model`)}
                    >
                        Use model
                    </button>
                    <button
                        className="settings-model-action"
                        type="button"
                        disabled={busy || !selected}
                        onClick={() => action('load', { model: selected }, `${selected} loaded into memory`)}
                        title="Pre-warm model in memory"
                    >
                        Load
                    </button>
                    <button
                        className="settings-model-action"
                        type="button"
                        disabled={busy || !selected}
                        onClick={() => action('unload', { model: selected }, `${selected} unloaded from memory`)}
                        title="Evict model from memory"
                    >
                        Unload
                    </button>
                </div>
            </div>

            {/* Download Ollama Models Dropdown Section */}
            <div className="settings-model-download-section">
                <div className="settings-model-download-header">
                    <label className="settings-label">Available Models to Download (Ollama)</label>
                    <button
                        type="button"
                        className="settings-model-link-btn"
                        onClick={() => setIsCustomMode(!isCustomMode)}
                    >
                        {isCustomMode ? 'Choose from list' : '+ Custom model name'}
                    </button>
                </div>

                <div className="settings-model-row">
                    {isCustomMode ? (
                        <input
                            className="settings-input"
                            aria-label="Custom model name to download"
                            placeholder="e.g. llama3.2:3b or qwen2.5:7b"
                            value={customModelName}
                            onChange={event => setCustomModelName(event.target.value)}
                            disabled={busy}
                        />
                    ) : (
                        <select
                            className="settings-input"
                            aria-label="Available models to download"
                            value={selectedDownloadModel}
                            onChange={event => setSelectedDownloadModel(event.target.value)}
                            disabled={busy}
                        >
                            {state?.hardware?.recommended_models && state.hardware.recommended_models.length > 0 && (
                                <optgroup label="⭐ Recommended for your hardware">
                                    {state.hardware.recommended_models.map(model => {
                                        const downloaded = (state.models || []).includes(model);
                                        return (
                                            <option key={`rec-${model}`} value={model}>
                                                {model} {downloaded ? '✓ (Downloaded)' : ''}
                                            </option>
                                        );
                                    })}
                                </optgroup>
                            )}
                            {POPULAR_OLLAMA_MODELS.map(group => (
                                <optgroup key={group.category} label={group.category}>
                                    {group.models.map(model => {
                                        const downloaded = (state?.models || []).includes(model);
                                        return (
                                            <option key={model} value={model}>
                                                {model} {downloaded ? '✓ (Downloaded)' : ''}
                                            </option>
                                        );
                                    })}
                                </optgroup>
                            ))}
                        </select>
                    )}
                    <button
                        className="settings-model-action settings-download-btn"
                        type="button"
                        disabled={busy || !targetDownloadModel || isTargetAlreadyDownloaded}
                        onClick={pull}
                    >
                        <Download size={14} />
                        <span>{isTargetAlreadyDownloaded ? 'Downloaded' : 'Download'}</span>
                    </button>
                </div>

                {downloadProgress && (
                    <div className="settings-download-progress-container" role="progressbar" aria-valuenow={downloadProgress.percent} aria-valuemin={0} aria-valuemax={100}>
                        <div className="settings-download-progress-header">
                            <span className="settings-download-progress-status">
                                <Loader2 size={13} className="spin" />
                                <span>{downloadProgress.status}</span>
                            </span>
                            <span className="settings-download-progress-meta">
                                {downloadProgress.total > 0
                                    ? `${formatBytes(downloadProgress.completed)} / ${formatBytes(downloadProgress.total)} (${downloadProgress.percent}%)`
                                    : downloadProgress.percent > 0 ? `${downloadProgress.percent}%` : 'Connecting…'}
                            </span>
                        </div>
                        <div className="settings-progress-track">
                            <div
                                className={`settings-progress-fill ${downloadProgress.total > 0 ? '' : 'indeterminate'}`}
                                style={{ width: downloadProgress.total > 0 ? `${downloadProgress.percent}%` : undefined }}
                            />
                        </div>
                    </div>
                )}
            </div>

            {/* Downloaded Models List with Delete Option in front of each model */}
            <div className="settings-installed-models-section">
                <div className="settings-installed-header">
                    <span className="settings-label">
                        Downloaded Models ({state?.models?.length || 0})
                    </span>
                </div>

                {(state?.models || []).length === 0 ? (
                    <div className="settings-model-empty">
                        <AlertCircle size={15} />
                        <span>No models downloaded yet. Choose a model above to download.</span>
                    </div>
                ) : (
                    <div className="settings-models-list">
                        {(state?.models || []).map(model => {
                            const isActive = model === state?.active_model;
                            const isDeleting = deletingModel === model;

                            return (
                                <div
                                    key={model}
                                    className={`settings-model-item ${isActive ? 'active-model-item' : ''}`}
                                >
                                    <div className="settings-model-item-info">
                                        <span className="settings-model-item-name">{model}</span>
                                        {isActive && (
                                            <span className="settings-model-active-badge">
                                                <CheckCircle2 size={12} /> Active
                                            </span>
                                        )}
                                    </div>

                                    <div className="settings-model-item-actions">
                                        {!isActive && (
                                            <button
                                                type="button"
                                                className="settings-model-mini-btn"
                                                disabled={busy}
                                                onClick={() => action('select', { model }, `${model} selected as active model`)}
                                                title="Set as active model"
                                            >
                                                Use
                                            </button>
                                        )}
                                        <button
                                            type="button"
                                            className="settings-model-delete-btn"
                                            disabled={busy || isDeleting}
                                            onClick={() => deleteModel(model)}
                                            title={`Delete ${model} from storage`}
                                            aria-label={`Delete ${model}`}
                                        >
                                            <Trash2 size={13} />
                                            <span>{isDeleting ? 'Deleting…' : 'Delete'}</span>
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>

            {state?.hardware?.recommended_profile && (
                <p className="settings-model-note">
                    Hardware profile: <strong>{state.hardware.recommended_profile.replaceAll('_', ' ')}</strong>.
                    Suggested: {state.hardware.recommended_models?.slice(0, 3).join(', ')}.
                </p>
            )}

            {status && (
                <p className="settings-model-status" role="status">
                    {status}
                </p>
            )}

            {error && (
                <p className="settings-model-error" role="alert">
                    {error}
                </p>
            )}
        </div>
    );
}
