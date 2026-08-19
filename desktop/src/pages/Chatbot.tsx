import React, { useState, useEffect, useRef } from 'react';
import { Bot, Download, FileText } from 'lucide-react';
import { MessageBubble } from '../components/chat/MessageBubble';
import { TypingIndicator } from '../components/chat/TypingIndicator';
import { SuggestionChips } from '../components/chat/SuggestionChips';
import { Composer } from '../components/chat/Composer';
import '../Chat.css';

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'error';
    content: string;
    route?: string;
    sources?: { source_file: string; source_row: number; label: string }[];
    timing?: number;
}

export default function Chatbot() {
    const [messages, setMessages] = useState<Message[]>([]);
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

            if (!res.ok) throw new Error('Network response was not ok');

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

    const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend(input);
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
                        <SuggestionChips onSelect={handleSuggestionClick} />
                    </div>
                ) : (
                    messages.map((msg, index) => (
                        <MessageBubble key={msg.id} msg={msg} isLatest={index === messages.length - 1} />
                    ))
                )}

                {isLoading && <TypingIndicator />}

                <div ref={messagesEndRef} />
            </div>

            <Composer
                input={input}
                setInput={setInput}
                handleSend={handleSend}
                isLoading={isLoading}
                handleKeyDown={handleKeyDown}
            />
        </div>
    );
}
