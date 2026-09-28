import { useState, useEffect } from 'react';
import { Database, Trash2, FileText, RefreshCw, AlertCircle, CheckCircle } from 'lucide-react';

export interface IngestedFile {
    file_id: string;
    filename: string;
    file_path: string;
    file_hash: string;
    chunk_count: number;
    domain: string;
    strategy: string;
    status: string;
    ingested_at: string;
    file_size_bytes?: number;
}

interface Props {
    totalChunks: number | null;
    collectionName: string | null;
    onRefresh?: () => void;
}

export const KBStatus = ({ totalChunks: initialChunks, collectionName, onRefresh }: Props) => {
    const [files, setFiles] = useState<IngestedFile[]>([]);
    const [loading, setLoading] = useState(false);
    const [actionMsg, setActionMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
    const [totalChunks, setTotalChunks] = useState<number | null>(initialChunks);

    const fetchSources = async () => {
        setLoading(true);
        try {
            const res = await fetch('/api/kb/sources');
            if (res.ok) {
                const data = await res.json();
                setFiles(data.files || []);
                setTotalChunks(data.total_chunks ?? 0);
            }
        } catch (e) {
            console.error('Failed to load KB sources', e);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchSources();
    }, [initialChunks]);

    const handleUningest = async (file: IngestedFile) => {
        if (!window.confirm(`Are you sure you want to un-ingest "${file.filename}"? This will remove all its ${file.chunk_count} chunks from the local search index.`)) {
            return;
        }

        try {
            const res = await fetch(`/api/kb/sources/${encodeURIComponent(file.file_id)}`, {
                method: 'DELETE'
            });
            if (!res.ok) throw new Error('Failed to un-ingest file');

            setActionMsg({ type: 'success', text: `Successfully un-ingested ${file.filename}` });
            await fetchSources();
            if (onRefresh) onRefresh();
            setTimeout(() => setActionMsg(null), 4000);
        } catch (err: any) {
            setActionMsg({ type: 'error', text: err.message || 'Error un-ingesting file' });
            setTimeout(() => setActionMsg(null), 4000);
        }
    };

    const hasData = totalChunks !== null && totalChunks > 0;

    return (
        <div className="kb-manager-card" style={{
            background: 'var(--surface-bg)',
            border: '1px solid var(--border-color)',
            borderRadius: '16px',
            padding: '1.25rem 1.5rem',
            boxShadow: 'var(--elevation-1)',
            marginTop: '1.5rem'
        }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div className={`kb-dot ${hasData ? 'active' : 'empty'}`} style={{
                        width: '10px',
                        height: '10px',
                        borderRadius: '50%',
                        background: hasData ? 'var(--brand-green)' : 'var(--text-tertiary)'
                    }} />
                    <div>
                        <h4 style={{ margin: 0, fontSize: '1rem', fontWeight: 600, color: 'var(--text-primary)' }}>
                            Ingested Knowledge Base Sources
                        </h4>
                        <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                            {totalChunks === null ? 'Checking...' : `${totalChunks.toLocaleString()} embedded chunks · ${collectionName || 'llm_konnect_kb'}`}
                        </span>
                    </div>
                </div>
                <button
                    onClick={fetchSources}
                    className="btn-secondary"
                    style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '0.35rem' }}
                    disabled={loading}
                    title="Refresh sources list"
                >
                    <RefreshCw size={13} className={loading ? 'spinning' : ''} /> Refresh
                </button>
            </div>

            {actionMsg && (
                <div style={{
                    padding: '0.6rem 0.85rem',
                    borderRadius: '8px',
                    fontSize: '0.85rem',
                    marginBottom: '1rem',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    background: actionMsg.type === 'success' ? 'var(--brand-green-container)' : 'rgba(239, 68, 68, 0.12)',
                    color: actionMsg.type === 'success' ? 'var(--brand-green-text)' : '#f87171',
                    border: `1px solid ${actionMsg.type === 'success' ? 'var(--brand-green-border)' : 'rgba(239, 68, 68, 0.3)'}`
                }}>
                    {actionMsg.type === 'success' ? <CheckCircle size={15} /> : <AlertCircle size={15} />}
                    {actionMsg.text}
                </div>
            )}

            {files.length === 0 ? (
                <div style={{
                    padding: '1.5rem',
                    textAlign: 'center',
                    background: 'var(--surface-container)',
                    borderRadius: '12px',
                    border: '1px dashed var(--border-color)',
                    color: 'var(--text-secondary)',
                    fontSize: '0.875rem'
                }}>
                    <Database size={24} style={{ margin: '0 auto 0.5rem', opacity: 0.5 }} />
                    <p style={{ margin: 0 }}>No files currently embedded in the Knowledge Base.</p>
                    <span style={{ fontSize: '0.78rem', color: 'var(--text-tertiary)' }}>Complete the wizard steps above to connect and ingest your dataset.</span>
                </div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                        <thead>
                            <tr style={{ borderBottom: '1px solid var(--border-color)', color: 'var(--text-secondary)', textAlign: 'left' }}>
                                <th style={{ padding: '0.6rem 0.75rem', fontWeight: 600 }}>File Name</th>
                                <th style={{ padding: '0.6rem 0.75rem', fontWeight: 600 }}>Domain</th>
                                <th style={{ padding: '0.6rem 0.75rem', fontWeight: 600 }}>Chunks</th>
                                <th style={{ padding: '0.6rem 0.75rem', fontWeight: 600 }}>Ingested At</th>
                                <th style={{ padding: '0.6rem 0.75rem', fontWeight: 600, textAlign: 'right' }}>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {files.map(file => (
                                <tr key={file.file_id} style={{ borderBottom: '1px solid var(--border-color)' }}>
                                    <td style={{ padding: '0.65rem 0.75rem', fontWeight: 500, color: 'var(--text-primary)' }}>
                                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem' }}>
                                            <FileText size={14} style={{ color: 'var(--brand-green)' }} />
                                            <span>{file.filename}</span>
                                        </div>
                                    </td>
                                    <td style={{ padding: '0.65rem 0.75rem' }}>
                                        <span style={{
                                            padding: '0.15rem 0.55rem',
                                            borderRadius: '999px',
                                            fontSize: '0.72rem',
                                            fontWeight: 600,
                                            background: 'var(--brand-green-container)',
                                            color: 'var(--brand-green-text)',
                                            border: '1px solid var(--brand-green-border)'
                                        }}>
                                            {file.domain.toUpperCase()}
                                        </span>
                                    </td>
                                    <td style={{ padding: '0.65rem 0.75rem', color: 'var(--text-secondary)', fontFamily: 'monospace' }}>
                                        {file.chunk_count.toLocaleString()}
                                    </td>
                                    <td style={{ padding: '0.65rem 0.75rem', color: 'var(--text-tertiary)', fontSize: '0.78rem' }}>
                                        {new Date(file.ingested_at).toLocaleDateString()} {new Date(file.ingested_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                    </td>
                                    <td style={{ padding: '0.65rem 0.75rem', textAlign: 'right' }}>
                                        <button
                                            onClick={() => handleUningest(file)}
                                            style={{
                                                background: 'transparent',
                                                border: '1px solid rgba(239, 68, 68, 0.4)',
                                                borderRadius: '6px',
                                                color: '#ef4444',
                                                padding: '0.25rem 0.6rem',
                                                fontSize: '0.75rem',
                                                cursor: 'pointer',
                                                display: 'inline-flex',
                                                alignItems: 'center',
                                                gap: '0.25rem',
                                                transition: 'all 0.15s'
                                            }}
                                            title="Un-ingest and remove chunks from local index"
                                            onMouseOver={e => e.currentTarget.style.background = 'rgba(239, 68, 68, 0.12)'}
                                            onMouseOut={e => e.currentTarget.style.background = 'transparent'}
                                        >
                                            <Trash2 size={12} /> Un-ingest
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
};

