import { User, Bot, AlertCircle, FileText } from 'lucide-react';

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'error';
    content: string;
    route?: string;
    sources?: { source_file: string; source_row: number; label: string }[];
    timing?: number;
}

export const MessageBubble = ({ msg, isLatest }: { msg: Message, isLatest: boolean }) => (
    <div className={`message-wrapper ${msg.role} ${isLatest ? 'latest' : ''}`}>
        <div className="message-content">
            <div className={`avatar ${msg.role === 'user' ? 'user' : 'bot'}`}>
                {msg.role === 'user' ? <User size={16} /> : msg.role === 'error' ? <AlertCircle size={16} /> : <Bot size={16} />}
            </div>
            <div>
                <div className="bubble">
                    {msg.content}
                </div>
                {(msg.route || (msg.sources && msg.sources.length > 0)) && (
                    <div className="reply-meta">
                        {msg.route && <span className="monospaced route-chip">{msg.route}</span>}
                        {msg.sources && msg.sources.length > 0 && msg.sources.map((s, idx) => (
                            <div key={idx} className="source-card">
                                <FileText size={12} className="icon" />
                                <span className="monospaced">{s.label || s.source_file} &middot; row #{s.source_row}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    </div>
);
