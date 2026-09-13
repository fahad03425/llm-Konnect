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

    return (
        <div className={`message-wrapper ${msg.role} ${isLatest ? 'latest' : ''}`}>
            <div className="message-content">
                <div className={`avatar ${msg.role === 'user' ? 'user' : msg.role === 'error' ? 'error' : 'bot'}`}>
                    {msg.role === 'user' ? <User size={16} /> : msg.role === 'error' ? <AlertCircle size={16} /> : <Bot size={16} />}
                </div>
                <div>
                    <div className="bubble">
                        {msg.content}
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
