import React, { useState, useEffect, useRef } from 'react';
import { Send, User, Bot, AlertCircle, Download, FileText, Lock, ShieldCheck, Zap } from 'lucide-react';
import './Chat.css';

// Placeholder pages
export function Dashboard() {
    return (
        <div className="placeholder-card">
            <h2>Welcome to LLM-KONNECT</h2>
            <p>Financial and Pharmacy analytics dashboard.</p>
        </div>
    );
}

export function ConnectSource() {
    return (
        <div className="placeholder-card">
            <h2>Connect Source</h2>
            <p>Upload files or connect APIs. (Coming soon)</p>
        </div>
    );
}

export function Chatbot() {
    const [messages, setMessages] = useState<{
        id: string;
        role: 'user' | 'assistant' | 'error';
        content: string;
        route?: string;
        sources?: { source_file: string; source_row: number; label: string }[];
        timing?: number;
    }[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [sessionId] = useState(() => `sess-${Math.random().toString(36).substring(2, 10)}`);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isLoading]);

    const handleSend = async (text: string = input) => {
        if (!text.trim() || isLoading) return;

        const userMsg = text.trim();
        setInput('');
        setMessages(prev => [...prev, { id: Date.now().toString(), role: 'user', content: userMsg }]);
        setIsLoading(true);

        try {
            const res = await fetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ question: userMsg, session_id: sessionId, domain: 'pharmacy' })
            });

            if (!res.ok) {
                throw new Error('Network response was not ok');
            }

            const data = await res.json();

            setMessages(prev => [...prev, {
                id: Date.now().toString() + 'bot',
                role: 'assistant',
                content: data.answer,
                route: data.route,
                sources: data.sources,
                timing: data.timing
            }]);
        } catch (error) {
            setMessages(prev => [...prev, {
                id: Date.now().toString() + 'err',
                role: 'error',
                content: "Couldn't reach the local engine — is the backend running?"
            }]);
        } finally {
            setIsLoading(false);
        }
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleSuggestionClick = (suggestion: string) => {
        setInput(suggestion);
        handleSend(suggestion);
    };

    return (
        <div className="chat-layout">
            <div className="chat-header">
                <div className="chat-header-title">
                    <h2>RAG Chatbot</h2>
                    <span className="monospaced model-chip">[Model: Local Qwen]</span>
                </div>
                <div className="chat-actions">
                    <button className="action-btn" title="Coming soon" disabled>
                        <Download size={14} /> Export Chat
                    </button>
                    <button className="action-btn" title="Coming soon" disabled>
                        <FileText size={14} /> View Audit Log
                    </button>
                </div>
            </div>

            <div className="chat-messages">
                {messages.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">
                            <Bot size={24} />
                        </div>
                        <h3>Welcome to LLM-KONNECT</h3>
                        <p>Ask a question about your pharmacy data to get started.</p>
                        <div className="suggestions-grid">
                            <button className="suggestion-card" onClick={() => handleSuggestionClick("What is my total revenue?")}>
                                What is my total revenue?
                            </button>
                            <button className="suggestion-card" onClick={() => handleSuggestionClick("Which medicines are expiring soon?")}>
                                Which medicines are expiring soon?
                            </button>
                            <button className="suggestion-card" onClick={() => handleSuggestionClick("Show me revenue by month")}>
                                Show me revenue by month
                            </button>
                            <button className="suggestion-card" onClick={() => handleSuggestionClick("Give me a summary of my pharmacy data")}>
                                Give me a summary of my pharmacy data
                            </button>
                        </div>
                    </div>
                ) : (
                    messages.map((msg, index) => (
                        <div key={msg.id} className={`message-wrapper ${msg.role} ${index === messages.length - 1 ? 'latest' : ''}`}>
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
                    ))
                )}

                {isLoading && (
                    <div className="message-wrapper assistant">
                        <div className="message-content">
                            <div className="avatar bot">
                                <Bot size={16} />
                            </div>
                            <div className="bubble" style={{ display: 'flex', alignItems: 'center' }}>
                                <div className="typing-indicator">
                                    <div className="typing-dot"></div>
                                    <div className="typing-dot"></div>
                                    <div className="typing-dot"></div>
                                </div>
                            </div>
                        </div>
                    </div>
                )}

                <div ref={messagesEndRef} />
            </div>

            <div className="chat-input-wrapper">
                <div className="input-box">
                    <input
                        type="text"
                        className="chat-input"
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        placeholder="Type a plain-language question…"
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
        </div>
    );
}

export function ReportExport() {
    return (
        <div className="placeholder-card">
            <h2>Report Export</h2>
            <p className="monospaced" style={{ color: '#0D7377', marginTop: '1rem' }}>[ PLACEHOLDER / COMING SOON ]</p>
        </div>
    );
}
