import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Files,
    Database,
    UploadCloud,
    Trash2,
    RefreshCw,
    Search,
    CheckCircle2,
    HardDrive,
    Layers,
    MessageSquare,
    ExternalLink,
    AlertTriangle,
    Sparkles,
    AlertCircle,
    RotateCcw
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useFilePath } from '../context/FileContext';
import './UploadedFiles.css';

interface FileItem {
    file_id?: string;
    filename: string;
    file_path: string;
    file_size_bytes: number;
    file_size_formatted: string;
    extension: string;
    dir_type?: string;
    modified_at?: string;
    is_ingested: boolean;
    is_processing?: boolean;
    is_duplicate_of?: string | null;
    chunk_count: number;
    domain?: string;
    strategy?: string;
    status?: string;
    progress?: number;
    step_text?: string;
    error_message?: string | null;
    ingested_at?: string;
}

interface FilesResponse {
    files: FileItem[];
    total_files: number;
    total_ingested_files: number;
    total_chunks: number;
    total_size_formatted: string;
}

const UploadedFiles: React.FC = () => {
    const navigate = useNavigate();
    const { user, activeDomainMeta } = useUser();
    const { setActivePath } = useFilePath();

    const [filesData, setFilesData] = useState<FilesResponse>(() => {
        try {
            const cached = sessionStorage.getItem('llm_konnect_files_cache');
            if (cached) return JSON.parse(cached);
        } catch (_) {}
        return {
            files: [],
            total_files: 0,
            total_ingested_files: 0,
            total_chunks: 0,
            total_size_formatted: '0 B'
        };
    });
    const [loading, setLoading] = useState<boolean>(() => {
        try {
            return !sessionStorage.getItem('llm_konnect_files_cache');
        } catch (_) {
            return true;
        }
    });
    const [searchQuery, setSearchQuery] = useState<string>('');
    const [activeTab, setActiveTab] = useState<'all' | 'ingested' | 'not_ingested'>('all');
    const [actionLoadingFile, setActionLoadingFile] = useState<string | null>(null);

    // Modal state for delete confirmation
    const [deleteModalFile, setDeleteModalFile] = useState<FileItem | null>(null);

    // Toast state
    const [toast, setToast] = useState<{ message: string; type: 'success' | 'error' | 'info' } | null>(null);

    // Drag-and-drop state
    const [isDragging, setIsDragging] = useState<boolean>(false);
    const fileInputRef = useRef<HTMLInputElement>(null);

    const showToast = (message: string, type: 'success' | 'error' | 'info' = 'success') => {
        setToast({ message, type });
        setTimeout(() => setToast(null), 4000);
    };

    const fetchFiles = useCallback(async (isSilent: boolean = false) => {
        try {
            if (!isSilent && !sessionStorage.getItem('llm_konnect_files_cache')) {
                setLoading(true);
            }
            const res = await fetch('http://127.0.0.1:8756/api/files');
            if (!res.ok) throw new Error('Failed to fetch files');
            const data: FilesResponse = await res.json();
            setFilesData(data);
            try {
                sessionStorage.setItem('llm_konnect_files_cache', JSON.stringify(data));
            } catch (_) {}
        } catch (err: any) {
            if (!isSilent) {
                showToast(err.message || 'Error connecting to backend API', 'error');
            }
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        fetchFiles(false);
    }, [fetchFiles]);

    // Active polling: auto-poll every 1.2 seconds if any file is currently processing
    useEffect(() => {
        const hasProcessing = filesData.files.some(f => f.status === 'processing' || f.is_processing);
        if (!hasProcessing) return;

        const interval = setInterval(() => {
            fetchFiles(true);
        }, 1200);

        return () => clearInterval(interval);
    }, [filesData.files, fetchFiles]);

    // File Upload Handler
    const handleFileUpload = async (fileList: FileList | null) => {
        if (!fileList || fileList.length === 0) return;
        const file = fileList[0];
        const formData = new FormData();
        formData.append('file', file);

        try {
            setActionLoadingFile(file.name);
            showToast(`Uploading ${file.name}...`, 'info');
            const res = await fetch('http://127.0.0.1:8756/api/files/upload', {
                method: 'POST',
                body: formData
            });
            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || 'Upload failed');
            }
            showToast(`Uploaded ${file.name} successfully!`, 'success');
            await fetchFiles(false);
        } catch (err: any) {
            showToast(err.message || 'Failed to upload file', 'error');
        } finally {
            setActionLoadingFile(null);
            if (fileInputRef.current) fileInputRef.current.value = '';
        }
    };

    // Quick Ingest Handler with Instant Optimistic UI Update
    const handleQuickIngest = async (file: FileItem) => {
        // Optimistically set file as processing with initial progress
        setFilesData(prev => ({
            ...prev,
            files: prev.files.map(f => f.file_path === file.file_path ? {
                ...f,
                is_processing: true,
                status: 'processing',
                progress: 10,
                step_text: 'Starting background ingestion...'
            } : f)
        }));

        try {
            setActionLoadingFile(file.filename);
            showToast(`Started ingestion for ${file.filename}...`, 'info');
            const res = await fetch('http://127.0.0.1:8756/api/files/quick-ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: file.file_path,
                    domain: user?.domain || activeDomainMeta?.id || file.domain || 'pharmacy',
                    strategy: file.strategy || 'row',
                    file_id: file.file_id
                })
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || 'Ingestion failed');
            }

            const data = await res.json();
            showToast(data.message || `Ingesting ${file.filename} in background...`, 'info');
            await fetchFiles(true);
        } catch (err: any) {
            showToast(err.message || 'Ingestion error', 'error');
            await fetchFiles(true);
        } finally {
            setActionLoadingFile(null);
        }
    };

    // Un-ingest Handler
    const handleUningest = async (file: FileItem) => {
        try {
            setActionLoadingFile(file.filename);
            showToast(`Un-ingesting ${file.filename}...`, 'info');
            const res = await fetch('http://127.0.0.1:8756/api/files/un-ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_id: file.file_id,
                    file_path: file.file_path
                })
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || 'Un-ingest failed');
            }

            showToast(`Successfully un-ingested ${file.filename}`, 'success');
            await fetchFiles(false);
        } catch (err: any) {
            showToast(err.message || 'Un-ingest error', 'error');
        } finally {
            setActionLoadingFile(null);
        }
    };

    // Delete File Handler
    const confirmDeleteFile = async () => {
        if (!deleteModalFile) return;
        const target = deleteModalFile;
        try {
            setActionLoadingFile(target.filename);
            setDeleteModalFile(null);
            showToast(`Deleting ${target.filename}...`, 'info');

            const params = new URLSearchParams();
            if (target.file_path) params.append('file_path', target.file_path);
            if (target.file_id) params.append('file_id', target.file_id);
            if (target.filename) params.append('filename', target.filename);

            const res = await fetch(`http://127.0.0.1:8756/api/files?${params.toString()}`, {
                method: 'DELETE'
            });

            if (!res.ok) {
                const errData = await res.json().catch(() => ({}));
                throw new Error(errData.detail || 'Delete failed');
            }

            showToast(`Deleted ${target.filename} from disk and KB!`, 'success');
            await fetchFiles(false);
        } catch (err: any) {
            showToast(err.message || 'Delete error', 'error');
        } finally {
            setActionLoadingFile(null);
        }
    };

    // Navigate to Chatbot with File Filter
    const handleChatWithFile = (file: FileItem) => {
        setActivePath(file.file_path);
        navigate('/chat');
    };

    // Navigate to Connect Source for Custom Schema Mapping
    const handleConnectSourceWithFile = (file: FileItem) => {
        setActivePath(file.file_path);
        navigate('/connect');
    };

    // Format Icon Helper
    const renderFormatIcon = (ext: string) => {
        const cleanExt = ext.replace('.', '').toLowerCase();
        if (cleanExt === 'csv') return <div className="file-format-icon csv">CSV</div>;
        if (cleanExt === 'xlsx' || cleanExt === 'xls') return <div className="file-format-icon xlsx">XLSX</div>;
        if (cleanExt === 'json') return <div className="file-format-icon json">JSON</div>;
        if (cleanExt === 'db' || cleanExt === 'sqlite') return <div className="file-format-icon db">SQL</div>;
        return <div className="file-format-icon other">TXT</div>;
    };

    // Active Processing Files
    const processingFiles = filesData.files.filter(f => f.status === 'processing' || f.is_processing);

    // Filtered Files List
    const filteredFiles = filesData.files.filter(f => {
        const matchesSearch = f.filename.toLowerCase().includes(searchQuery.toLowerCase()) ||
            (f.file_path && f.file_path.toLowerCase().includes(searchQuery.toLowerCase()));

        if (!matchesSearch) return false;
        if (activeTab === 'ingested') return f.is_ingested;
        if (activeTab === 'not_ingested') return !f.is_ingested;
        return true;
    });

    return (
        <div className="uploaded-files-container">
            {/* Header & Stats */}
            <div className="files-header-banner">
                <div>
                    <h2 className="files-header-title">
                        <Files size={28} color="#0d7377" />
                        Uploaded Files & Knowledge Base
                    </h2>
                    <p className="files-header-subtitle">
                        Manage dataset files, track real-time vectorization progress into Chroma DB, and control your offline RAG knowledge base.
                    </p>
                </div>
            </div>

            {/* Live Ingestion Tracker Banner */}
            {processingFiles.length > 0 && (
                <div className="active-ingestion-banner">
                    <div className="ingestion-banner-header">
                        <div className="ingestion-banner-title">
                            <RefreshCw size={18} className="spin" />
                            <span>Active Ingestion in Progress ({processingFiles.length} file{processingFiles.length > 1 ? 's' : ''})</span>
                        </div>
                        <span className="live-pulse-badge">Live Indexing</span>
                    </div>
                    <div className="ingestion-banner-items">
                        {processingFiles.map(pf => {
                            const pct = Math.round(pf.progress || 15);
                            return (
                                <div key={pf.file_path} className="active-task-row">
                                    <div className="task-row-info">
                                        <span className="task-file-name">{pf.filename}</span>
                                        <span className="task-step-desc">{pf.step_text || 'Processing chunks & vector embeddings...'}</span>
                                        <span className="task-pct-label">{pct}%</span>
                                    </div>
                                    <div className="task-progress-track">
                                        <div
                                            className="task-progress-bar"
                                            style={{ width: `${Math.max(5, pct)}%` }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* Quick Metrics Bar */}
            <div className="files-stats-grid">
                <div className="files-stat-card">
                    <div className="stat-icon-wrapper" style={{ background: '#eff6ff', color: '#2563eb' }}>
                        <HardDrive size={22} />
                    </div>
                    <div className="stat-info">
                        <div className="stat-value">{filesData.total_files}</div>
                        <div className="stat-label">Total Files</div>
                    </div>
                </div>

                <div className="files-stat-card">
                    <div className="stat-icon-wrapper" style={{ background: '#ecfdf5', color: '#059669' }}>
                        <CheckCircle2 size={22} />
                    </div>
                    <div className="stat-info">
                        <div className="stat-value">{filesData.total_ingested_files}</div>
                        <div className="stat-label">Ingested in KB</div>
                    </div>
                </div>

                <div className="files-stat-card">
                    <div className="stat-icon-wrapper" style={{ background: '#f5f3ff', color: '#7c3aed' }}>
                        <Layers size={22} />
                    </div>
                    <div className="stat-info">
                        <div className="stat-value">{filesData.total_chunks.toLocaleString()}</div>
                        <div className="stat-label">Active Chunks</div>
                    </div>
                </div>

                <div className="files-stat-card">
                    <div className="stat-icon-wrapper" style={{ background: '#fffbeb', color: '#d97706' }}>
                        <Database size={22} />
                    </div>
                    <div className="stat-info">
                        <div className="stat-value">{filesData.total_size_formatted}</div>
                        <div className="stat-label">Storage Used</div>
                    </div>
                </div>
            </div>

            {/* Drag and Drop Upload Area */}
            <div
                className={`file-upload-dropzone ${isDragging ? 'drag-active' : ''}`}
                onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onDrop={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                    handleFileUpload(e.dataTransfer.files);
                }}
                onClick={() => fileInputRef.current?.click()}
            >
                <input
                    type="file"
                    ref={fileInputRef}
                    style={{ display: 'none' }}
                    accept=".csv,.xlsx,.xls,.json,.db,.sqlite,.txt"
                    onChange={(e) => handleFileUpload(e.target.files)}
                />
                <div className="upload-icon-circle">
                    <UploadCloud size={26} />
                </div>
                <div>
                    <div className="upload-dropzone-title">Click to upload or drag and drop files here</div>
                    <div className="upload-dropzone-subtitle">Supported: CSV, Excel (.xlsx), JSON, SQLite (.db)</div>
                </div>
            </div>

            {/* Toolbar: Search & Filter Tabs */}
            <div className="files-toolbar">
                <div className="files-filter-tabs">
                    <button
                        className={`filter-tab-btn ${activeTab === 'all' ? 'active' : ''}`}
                        onClick={() => setActiveTab('all')}
                    >
                        <span>All Files</span>
                        <span className="tab-badge">{filesData.total_files}</span>
                    </button>
                    <button
                        className={`filter-tab-btn ${activeTab === 'ingested' ? 'active' : ''}`}
                        onClick={() => setActiveTab('ingested')}
                    >
                        <span>🟢 Ingested</span>
                        <span className="tab-badge">{filesData.total_ingested_files}</span>
                    </button>
                    <button
                        className={`filter-tab-btn ${activeTab === 'not_ingested' ? 'active' : ''}`}
                        onClick={() => setActiveTab('not_ingested')}
                    >
                        <span>⚪ Not Ingested</span>
                        <span className="tab-badge">{filesData.total_files - filesData.total_ingested_files}</span>
                    </button>
                </div>

                <div className="files-search-actions">
                    <div className="files-search-input-wrapper">
                        <Search size={16} color="#94a3b8" />
                        <input
                            type="text"
                            placeholder="Filter by filename..."
                            className="files-search-input"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                        />
                    </div>
                    <button
                        className="btn-file-action map"
                        onClick={() => fetchFiles(false)}
                        title="Refresh file status from disk"
                    >
                        <RefreshCw size={14} className={loading ? 'spin' : ''} />
                        Refresh
                    </button>
                </div>
            </div>

            {/* Files Table */}
            <div className="files-table-wrapper">
                <table className="files-table">
                    <thead>
                        <tr>
                            <th style={{ width: '28%' }}>File Name</th>
                            <th style={{ width: '10%' }}>Size</th>
                            <th style={{ width: '30%' }}>Status in Knowledge Base</th>
                            <th style={{ width: '14%' }}>Domain / Strategy</th>
                            <th style={{ width: '10%' }}>Modified Date</th>
                            <th style={{ width: '8%', textAlign: 'right' }}>Actions</th>
                        </tr>
                    </thead>
                    <tbody>
                        {filteredFiles.length === 0 ? (
                            <tr>
                                <td colSpan={6} style={{ textAlign: 'center', padding: '3rem 1rem', color: '#94a3b8' }}>
                                    {loading ? 'Scanning files...' : 'No files found matching your search.'}
                                </td>
                            </tr>
                        ) : (
                            filteredFiles.map((file) => {
                                const isProcessing = file.status === 'processing' || file.is_processing || actionLoadingFile === file.filename;
                                const isFailed = file.status === 'failed';
                                const pct = Math.round(file.progress || 15);

                                return (
                                    <tr key={file.file_path} className={file.is_ingested ? 'ingested-row' : (isProcessing ? 'processing-row' : '')}>
                                        {/* Name & Icon */}
                                        <td>
                                            <div className="file-name-cell">
                                                {renderFormatIcon(file.extension)}
                                                <div>
                                                    <div className="file-primary-name">{file.filename}</div>
                                                    <div className="file-path-sub" title={file.file_path}>{file.file_path}</div>
                                                </div>
                                            </div>
                                        </td>

                                        {/* Size */}
                                        <td>
                                            <span style={{ fontWeight: 500, color: '#475569' }}>{file.file_size_formatted}</span>
                                        </td>

                                        {/* Status in KB */}
                                        <td>
                                            {isProcessing ? (
                                                <div className="table-progress-cell">
                                                    <div className="table-progress-header">
                                                        <span className="status-pill ingesting">
                                                            <RefreshCw size={12} className="spin" /> Processing
                                                        </span>
                                                        <span className="table-progress-pct">{pct}%</span>
                                                    </div>
                                                    <div className="table-progress-track">
                                                        <div
                                                            className="table-progress-fill"
                                                            style={{ width: `${Math.max(6, pct)}%` }}
                                                        />
                                                    </div>
                                                    <div className="table-progress-step" title={file.step_text}>
                                                        {file.step_text || 'Embedding records & saving vectors...'}
                                                    </div>
                                                </div>
                                            ) : isFailed ? (
                                                <div className="failed-status-cell">
                                                    <span className="status-pill failed" title={file.error_message || 'Ingestion encountered an error'}>
                                                        <AlertCircle size={13} />
                                                        Ingestion Failed
                                                    </span>
                                                    {file.error_message && (
                                                        <div className="failed-error-msg" title={file.error_message}>
                                                            {file.error_message}
                                                        </div>
                                                    )}
                                                </div>
                                            ) : file.is_ingested ? (
                                                <span className="status-pill ingested" title={`Ingested at ${file.ingested_at || 'recent'}`}>
                                                    <span className="pulse-dot" />
                                                    Ingested ({file.chunk_count.toLocaleString()} chunks)
                                                </span>
                                            ) : file.is_duplicate_of ? (
                                                <span
                                                    className="status-pill"
                                                    style={{ background: '#fffbeb', color: '#b45309', border: '1px solid #fde68a' }}
                                                    title={`Duplicate content identical to ${file.is_duplicate_of}`}
                                                >
                                                    ⚠️ Duplicate of {file.is_duplicate_of}
                                                </span>
                                            ) : (
                                                <span className="status-pill not-ingested">
                                                    Not Ingested
                                                </span>
                                            )}
                                        </td>

                                        {/* Domain / Strategy */}
                                        <td>
                                            <span style={{ fontSize: '0.8rem', color: '#64748b', fontWeight: 500 }}>
                                                {file.domain ? `${file.domain.toUpperCase()} / ${file.strategy || 'row'}` : '—'}
                                            </span>
                                        </td>

                                        {/* Date */}
                                        <td>
                                            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                                {file.modified_at ? new Date(file.modified_at).toLocaleDateString() : '—'}
                                            </span>
                                        </td>

                                        {/* Actions */}
                                        <td>
                                            <div className="file-actions-group">
                                                {isProcessing ? (
                                                    <button
                                                        className="btn-file-action"
                                                        style={{ background: '#eff6ff', color: '#2563eb', border: '1px solid #bfdbfe', cursor: 'wait' }}
                                                        disabled
                                                    >
                                                        <RefreshCw size={12} className="spin" /> {pct}%
                                                    </button>
                                                ) : isFailed ? (
                                                    <button
                                                        className="btn-file-action retry"
                                                        onClick={() => handleQuickIngest(file)}
                                                        title="Retry ingestion"
                                                    >
                                                        <RotateCcw size={13} />
                                                        Retry
                                                    </button>
                                                ) : !file.is_ingested ? (
                                                    <button
                                                        className="btn-file-action ingest"
                                                        onClick={() => handleQuickIngest(file)}
                                                        disabled={isProcessing || !!file.is_duplicate_of}
                                                        style={file.is_duplicate_of ? { opacity: 0.5, cursor: 'not-allowed', background: '#94a3b8' } : {}}
                                                        title={file.is_duplicate_of ? `Cannot ingest: identical content to ${file.is_duplicate_of} is already ingested.` : "Embed and add to Knowledge Base"}
                                                    >
                                                        <Sparkles size={14} />
                                                        Ingest
                                                    </button>
                                                ) : (
                                                    <>
                                                        <button
                                                            className="btn-file-action chat"
                                                            onClick={() => handleChatWithFile(file)}
                                                            title="Chat specifically with this file"
                                                        >
                                                            <MessageSquare size={14} />
                                                            Chat
                                                        </button>
                                                        <button
                                                            className="btn-file-action uningest"
                                                            onClick={() => handleUningest(file)}
                                                            disabled={isProcessing}
                                                            title="Remove chunks from Knowledge Base"
                                                        >
                                                            Un-ingest
                                                        </button>
                                                    </>
                                                )}

                                                <button
                                                    className="btn-file-action map"
                                                    onClick={() => handleConnectSourceWithFile(file)}
                                                    title="Configure custom schema and normalize"
                                                >
                                                    <ExternalLink size={13} />
                                                </button>

                                                <button
                                                    className="btn-file-action delete"
                                                    onClick={() => setDeleteModalFile(file)}
                                                    disabled={isProcessing}
                                                    title="Permanently delete file from disk & KB"
                                                >
                                                    <Trash2 size={16} />
                                                </button>
                                            </div>
                                        </td>
                                    </tr>
                                );
                            })
                        )}
                    </tbody>
                </table>
            </div>

            {/* Confirmation Modal for Delete */}
            {deleteModalFile && (
                <div className="modal-backdrop" onClick={() => setDeleteModalFile(null)}>
                    <div className="modal-content-card" onClick={(e) => e.stopPropagation()}>
                        <div className="modal-title">
                            <AlertTriangle size={22} color="#ef4444" />
                            Delete File & Data?
                        </div>
                        <div className="modal-body">
                            Are you sure you want to delete <strong>{deleteModalFile.filename}</strong>?
                            {deleteModalFile.is_ingested && (
                                <p style={{ marginTop: '0.5rem', color: '#b91c1c' }}>
                                    ⚠️ This file is currently <strong>ingested</strong> with {deleteModalFile.chunk_count.toLocaleString()} vector chunks. Deleting will un-ingest all its vectors from Chroma DB and delete the physical file from disk.
                                </p>
                            )}
                        </div>
                        <div className="modal-footer">
                            <button
                                className="btn-file-action map"
                                onClick={() => setDeleteModalFile(null)}
                            >
                                Cancel
                            </button>
                            <button
                                className="btn-file-action uningest"
                                style={{ background: '#ef4444', color: '#ffffff', borderColor: '#dc2626' }}
                                onClick={confirmDeleteFile}
                            >
                                Delete Permanently
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Toast feedback */}
            {toast && (
                <div className={`files-toast ${toast.type}`}>
                    {toast.type === 'success' && <CheckCircle2 size={16} />}
                    {toast.type === 'error' && <AlertTriangle size={16} />}
                    {toast.type === 'info' && <RefreshCw size={16} className="spin" />}
                    <span>{toast.message}</span>
                </div>
            )}
        </div>
    );
};

export default UploadedFiles;
