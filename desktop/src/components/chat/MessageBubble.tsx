import React, { useState } from 'react';
import { User, Bot, AlertCircle, FileText, ChevronDown } from 'lucide-react';

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'error';
    content: string;
    route?: string;
    sources?: { source_file: string; source_row: number; label: string }[];
    timing?: number;
}

export const MessageBubble: React.FC<{ msg: Message; isLatest: boolean }> = ({ msg, isLatest }) => {
    const [isSourcesOpen, setIsSourcesOpen] = useState(false);
    const hasSources = Boolean(msg.sources && msg.sources.length > 0);

    const formatMessageText = (content: string) => {
        if (!content) return null;
        
        // Normalize unseparated multi-table lists like "... Total amount For tbl_PurchaseHeader: - Date..."
        let normalized = content
            .replace(/([^\n])\s+(For\s+[\w_]+:)/g, '$1\n\n$2')
            .replace(/([^\n])\s+([•\-*]\s+)/g, '$1\n$2');

        const blocks = normalized.split(/\n\n+/);

        return blocks.map((block, bIdx) => {
            const lines = block.split(/\n/);
            const isBulletList = lines.every(l => l.trim().startsWith('- ') || l.trim().startsWith('• ') || l.trim().startsWith('* ') || l.trim().length === 0);

            if (isBulletList && lines.some(l => l.trim().length > 0)) {
                return (
                    <ul key={bIdx} style={{ margin: '0.4rem 0', paddingLeft: '1.4rem' }}>
                        {lines.filter(l => l.trim().length > 0).map((line, lIdx) => (
                            <li key={lIdx} style={{ margin: '0.2rem 0' }}>
                                {line.replace(/^[•\-*]\s*/, '').trim()}
                            </li>
                        ))}
                    </ul>
                );
            }

            // Check for section headers like "For tbl_...:" or "**...**"
            return (
                <div key={bIdx} style={{ marginBottom: bIdx < blocks.length - 1 ? '0.6rem' : 0 }}>
                    {lines.map((line, lIdx) => {
                        const trimmed = line.trim();
                        if (trimmed.startsWith('- ') || trimmed.startsWith('• ') || trimmed.startsWith('* ')) {
                            return (
                                <div key={lIdx} style={{ paddingLeft: '1.2rem', margin: '0.15rem 0' }}>
                                    • {trimmed.replace(/^[•\-*]\s*/, '')}
                                </div>
                            );
                        }
                        if (/^For\s+[\w_]+:/i.test(trimmed) || /^[\w_]+:$/i.test(trimmed) || /^\*\*.*\*\*$/.test(trimmed)) {
                            return (
                                <div key={lIdx} style={{ fontWeight: 600, color: 'var(--text-primary)', marginTop: lIdx > 0 ? '0.4rem' : 0, marginBottom: '0.2rem' }}>
                                    {trimmed.replace(/\*\*/g, '')}
                                </div>
                            );
                        }
                        return (
                            <div key={lIdx}>
                                {line}
                            </div>
                        );
                    })}
                </div>
            );
        });
    };

    return (
        <div className={`message-wrapper ${msg.role} ${isLatest ? 'latest' : ''}`}>
            <div className="message-content">
                <div className={`avatar ${msg.role === 'user' ? 'user' : msg.role === 'error' ? 'error' : 'bot'}`}>
                    {msg.role === 'user' ? <User size={16} /> : msg.role === 'error' ? <AlertCircle size={16} /> : <Bot size={16} />}
                </div>
                <div>
                    <div className="bubble">
                        {msg.role === 'user' ? msg.content : formatMessageText(msg.content)}
                    </div>
                    {(msg.route || hasSources || msg.timing !== undefined) && (
                        <div className="reply-meta">
                            <div className="reply-meta-chips">
                                {msg.route && <span className="monospaced route-chip">{msg.route}</span>}
                                {msg.timing !== undefined && (
                                    <span className="monospaced timing-chip">{msg.timing}s</span>
                                )}
                                {hasSources && (
                                    <button 
                                        className={`sources-toggle-btn ${isSourcesOpen ? 'open' : ''}`}
                                        onClick={() => setIsSourcesOpen(!isSourcesOpen)}
                                        title={isSourcesOpen ? "Collapse invoice references" : "Show referenced invoice records"}
                                    >
                                        <FileText size={12} className="sources-btn-icon" />
                                        <span>
                                            {msg.sources!.length} {msg.sources!.length === 1 ? 'Reference' : 'References'}
                                        </span>
                                        <ChevronDown size={13} className={`chevron-icon ${isSourcesOpen ? 'rotated' : ''}`} />
                                    </button>
                                )}
                            </div>

                            {hasSources && isSourcesOpen && (
                                <div className="sources-dropdown-list">
                                    {msg.sources!.map((s, idx) => {
                                        const hasSourceFile = s.source_file && s.source_file !== 'unknown' && !s.label?.includes(s.source_file);
                                        const displayText = hasSourceFile ? `${s.source_file} · ${s.label}` : (s.label || s.source_file || 'Record');
                                        return (
                                            <div 
                                                key={idx} 
                                                className="source-card" 
                                                title={`Dataset: ${s.source_file || 'N/A'}${s.source_row !== undefined && s.source_row !== null ? `, Row: #${s.source_row}` : ''}`}
                                            >
                                                <FileText size={12} className="icon" />
                                                <span className="monospaced">
                                                    {displayText}
                                                    {s.source_row !== undefined && s.source_row !== null && !displayText.includes(`row #${s.source_row}`) ? ` · row #${s.source_row}` : ''}
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};
