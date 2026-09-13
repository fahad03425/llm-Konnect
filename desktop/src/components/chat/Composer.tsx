import React from 'react';
import { Send, Lock, ShieldCheck, Zap, FileText, Globe, X } from 'lucide-react';

export interface ScopeFile {
    file_id: string;
    filename: string;
    chunk_count: number;
}

interface ComposerProps {
    input: string;
    setInput: (val: string) => void;
    handleSend: (text?: string) => void;
    isLoading: boolean;
    handleKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
    availableFiles?: ScopeFile[];
    selectedFileId?: string | null;
    onSelectFile?: (fileId: string | null) => void;
}

export const Composer = ({
    input,
    setInput,
    handleSend,
    isLoading,
    handleKeyDown,
    availableFiles = [],
    selectedFileId = null,
    onSelectFile
}: ComposerProps) => {
    const selectedFile = availableFiles.find(f => f.file_id === selectedFileId);

    return (
        <div className="chat-input-wrapper">
            {availableFiles.length > 0 && onSelectFile && (
                <div className="scope-selector-container">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: '#4B5563' }}>
                        {selectedFile ? <FileText size={14} color="#0D7377" /> : <Globe size={14} color="#6B7280" />}
                        <span style={{ fontWeight: 600 }}>Data Scope:</span>
                    </div>
                    <select
                        className="scope-select"
                        value={selectedFileId || 'all'}
                        onChange={e => onSelectFile(e.target.value === 'all' ? null : e.target.value)}
                        disabled={isLoading}
                    >
                        <option value="all">🌐 All Knowledge Base ({availableFiles.reduce((acc, f) => acc + f.chunk_count, 0).toLocaleString()} chunks)</option>
                        {availableFiles.map(f => (
                            <option key={f.file_id} value={f.file_id}>
                                📄 {f.filename} ({f.chunk_count} chunks)
                            </option>
                        ))}
                    </select>
                    {selectedFile && (
                        <span className="scope-badge">
                            Scoped: {selectedFile.filename}
                            <button
                                className="scope-clear-btn"
                                onClick={() => onSelectFile(null)}
                                title="Reset scope to All Files"
                            >
                                <X size={12} />
                            </button>
                        </span>
                    )}
                </div>
            )}

            <div className="input-box">
                <input
                    type="text"
                    className="chat-input"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={selectedFile ? `Ask question specifically about ${selectedFile.filename}…` : "Type a plain-language question…"}
                    disabled={isLoading}
                />
                <button
                    className="send-btn"
                    onClick={() => handleSend(input)}
                    disabled={!input.trim() || isLoading}
                >
                    <Send size={16} />
                </button>
            </div>
            <div className="chat-footer">
                <div className="footer-item">
                    <Lock size={12} /> End-to-End Encrypted
                </div>
                <div className="footer-item">
                    <ShieldCheck size={12} /> No data leaves your machine
                </div>
                <div className="footer-item">
                    <Zap size={12} /> Deterministic Output
                </div>
            </div>
        </div>
    );
};
