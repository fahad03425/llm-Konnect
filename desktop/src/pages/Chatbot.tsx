import React, { useState, useEffect, useRef } from 'react';
import { Bot, Download, Plus, History, ArrowDown } from 'lucide-react';
import { MessageBubble } from '../components/chat/MessageBubble';
import { TypingIndicator } from '../components/chat/TypingIndicator';
import { SuggestionChips } from '../components/chat/SuggestionChips';
import { Composer, type ScopeFile } from '../components/chat/Composer';
import { ChatHistorySection, type ChatSessionMeta } from '../components/chat/ChatHistorySection';
import { useUser } from '../context/UserContext';
import '../Chat.css';

interface Message {
    id: string;
    role: 'user' | 'assistant' | 'error';
    content: string;
    route?: string;
    sources?: { source_file: string; source_row: number; label: string }[];
    timing?: number;
    timestamp?: string;
}

export default function Chatbot() {
    const { user, activeDomainMeta } = useUser();
    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [availableFiles, setAvailableFiles] = useState<ScopeFile[]>([]);
    const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState(() => `sess-${Math.random().toString(36).substring(2, 10)}`);
    const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);
    const [isLoadingSessions, setIsLoadingSessions] = useState(false);

    const messagesContainerRef = useRef<HTMLDivElement>(null);
    const chatPanelRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        if (messagesContainerRef.current) {
            messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
        }
    };

    const scrollToTop = () => {
        chatPanelRef.current?.scrollIntoView({ behavior: 'smooth' });
    };

    const scrollToHistory = () => {
        const historyEl = document.getElementById('chat-history-section');
        historyEl?.scrollIntoView({ behavior: 'smooth' });
    };

    const fetchSources = async () => {
        try {
            const res = await fetch(`/api/kb/sources?domain=${encodeURIComponent(user.domain)}`);
            if (res.ok) {
                const data = await res.json();
                const files: ScopeFile[] = (data.files || []).map((f: any) => ({
                    file_id: f.file_id,
                    filename: f.filename,
                    chunk_count: f.chunk_count
                }));
                setAvailableFiles(files);
            }
        } catch (e) {
            console.error('Could not load sources for chat scoping', e);
        }
    };

    const fetchSessions = async () => {
        setIsLoadingSessions(true);
        try {
            const res = await fetch('/api/chat/sessions');
            if (res.ok) {
                const data = await res.json();
                setSessions(data.sessions || []);
            }
        } catch (e) {
            console.error('Failed to load past chat sessions', e);
        } finally {
            setIsLoadingSessions(false);
        }
    };

    const saveCurrentSession = async (currentMsgs: Message[], targetSessionId: string = sessionId) => {
        if (!currentMsgs || currentMsgs.length === 0) return;
        try {
            await fetch(`/api/chat/sessions/${targetSessionId}/save`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: currentMsgs,
                    domain: user.domain
                })
            });
            fetchSessions();
        } catch (e) {
            console.error('Error auto-saving session', e);
        }
    };

    useEffect(() => {
        document.title = `${activeDomainMeta.name} RAG Chatbot — LLM-KONNECT`;
        scrollToBottom();
        fetchSources();
    }, [messages, isLoading, activeDomainMeta.name, user.domain]);

    useEffect(() => {
        fetchSessions();
    }, [user.domain]);

    const handleNewChat = () => {
        const newId = `sess-${Math.random().toString(36).substring(2, 10)}`;
        setSessionId(newId);
        setMessages([]);
        setInput('');
        setSelectedFileId(null);
        scrollToTop();
    };

    const handleResumeSession = async (resumeId: string) => {
        if (resumeId === sessionId && messages.length > 0) {
            scrollToTop();
            return;
        }

        try {
            const res = await fetch(`/api/chat/sessions/${resumeId}`);
            if (res.ok) {
                const data = await res.json();
                setSessionId(resumeId);
                setMessages(data.messages || []);
                setInput('');
                scrollToTop();
            }
        } catch (e) {
            console.error('Failed to load session details', e);
        }
    };

    const handleDeleteSession = async (delId: string) => {
        try {
            const res = await fetch(`/api/chat/sessions/${delId}`, { method: 'DELETE' });
            if (res.ok) {
                if (sessionId === delId) {
                    handleNewChat();
                }
                fetchSessions();
            }
        } catch (e) {
            console.error('Failed to delete session', e);
        }
    };

    const handleClearAllSessions = async () => {
        try {
            const res = await fetch('/api/chat/sessions', { method: 'DELETE' });
            if (res.ok) {
                handleNewChat();
                fetchSessions();
            }
        } catch (e) {
            console.error('Failed to clear sessions', e);
        }
    };

    const handleExportChat = () => {
        if (messages.length === 0) return;
        const exportData = {
            session_id: sessionId,
            domain: user.domain,
            exported_at: new Date().toISOString(),
            messages: messages
        };
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `chat-${sessionId}-${user.domain}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleSend = async (text: string = input) => {
        if (!text.trim() || isLoading) return;

        const userMsg = text.trim();
        const userMsgObj: Message = { 
            id: Date.now().toString(), 
            role: 'user', 
            content: userMsg,
            timestamp: new Date().toISOString()
        };

        setInput('');
        const updatedMsgs = [...messages, userMsgObj];
        setMessages(updatedMsgs);
        setIsLoading(true);

        const botMsgId = Date.now().toString() + 'bot';
        let botAnswer = '';
        let route = 'rag';
        let sources: any[] = [];
        const startTime = Date.now();

        try {
            const payload: any = {
                question: userMsg,
                session_id: sessionId,
                domain: user.domain
            };
            if (selectedFileId) {
                payload.file_ids = [selectedFileId];
            }

            const res = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (!res.ok || !res.body) {
                throw new Error('Streaming not available, falling back');
            }

            // Create placeholder assistant bubble
            setMessages(prev => [...prev, {
                id: botMsgId,
                role: 'assistant',
                content: '',
                route: '',
                sources: [],
                timestamp: new Date().toISOString()
            }]);

            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop() || '';

                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const parsed = JSON.parse(line);
                        if (parsed.chunk) {
                            botAnswer += parsed.chunk;
                        }
                        if (parsed.route) route = parsed.route;
                        if (parsed.sources && parsed.sources.length > 0) sources = parsed.sources;

                        setMessages(prev => prev.map(m => m.id === botMsgId ? {
                            ...m,
                            content: botAnswer,
                            route: route,
                            sources: sources,
                            timing: Number(((Date.now() - startTime) / 1000).toFixed(2))
                        } : m));
                    } catch {
                        botAnswer += line;
                        setMessages(prev => prev.map(m => m.id === botMsgId ? {
                            ...m,
                            content: botAnswer,
                            timing: Number(((Date.now() - startTime) / 1000).toFixed(2))
                        } : m));
                    }
                }
            }

            if (buffer.trim()) {
                try {
                    const parsed = JSON.parse(buffer);
                    if (parsed.chunk) botAnswer += parsed.chunk;
                    if (parsed.route) route = parsed.route;
                    if (parsed.sources && parsed.sources.length > 0) sources = parsed.sources;
                } catch {
                    botAnswer += buffer;
                }
            }

            const finalAssistantMsg: Message = {
                id: botMsgId,
                role: 'assistant',
                content: botAnswer,
                route: route,
                sources: sources,
                timing: Number(((Date.now() - startTime) / 1000).toFixed(2)),
                timestamp: new Date().toISOString()
            };

            const allFinalMsgs = [...updatedMsgs, finalAssistantMsg];
            setMessages(allFinalMsgs);
            saveCurrentSession(allFinalMsgs, sessionId);

        } catch {
            // Non-streaming fallback
            try {
                const fallbackPayload: any = {
                    question: userMsg,
                    session_id: sessionId,
                    domain: user.domain
                };
                if (selectedFileId) fallbackPayload.file_ids = [selectedFileId];

                const fallbackRes = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(fallbackPayload)
                });
                if (fallbackRes.ok) {
                    const data = await fallbackRes.json();
                    const assistantMsg: Message = {
                        id: botMsgId,
                        role: 'assistant',
                        content: data.answer,
                        route: data.route,
                        sources: data.sources,
                        timing: data.timing,
                        timestamp: new Date().toISOString()
                    };
                    const allFallbackMsgs = [...updatedMsgs, assistantMsg];
                    setMessages(allFallbackMsgs);
                    saveCurrentSession(allFallbackMsgs, sessionId);
                    return;
                }
            } catch {}

            const errorMsg: Message = {
                id: Date.now().toString() + 'err',
                role: 'error',
                content: "Couldn't reach the local engine — is the backend running?",
                timestamp: new Date().toISOString()
            };
            setMessages(prev => [...prev.filter(m => m.id !== botMsgId), errorMsg]);
        } finally {
            setIsLoading(false);
            fetchSessions();
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
        <div className="chat-page-container">
            {/* Top Interactive Chat Panel */}
            <div className="chat-layout" ref={chatPanelRef}>
                <div className="chat-header">
                    <div className="chat-header-title">
                        <h2>RAG Chatbot</h2>
                        <span className="monospaced model-chip" style={{ background: 'rgba(16, 185, 129, 0.12)', color: 'var(--accent-teal)', border: '1px solid rgba(16, 185, 129, 0.3)' }}>
                            {activeDomainMeta.icon} {activeDomainMeta.name}
                        </span>
                        <span className="monospaced model-chip">[Model: Local Qwen]</span>
                    </div>
                    <div className="chat-actions">
                        <button 
                            className="btn-new-chat-primary"
                            onClick={handleNewChat}
                            title="Start a fresh conversation"
                        >
                            <Plus size={15} /> New Chat
                        </button>
                        <button 
                            className="action-btn"
                            onClick={scrollToHistory}
                            title="Scroll down to view past conversations"
                        >
                            <History size={14} /> Past Chats ({sessions.length})
                        </button>
                        <button 
                            className="action-btn" 
                            onClick={handleExportChat}
                            disabled={messages.length === 0}
                            title="Export current chat as JSON"
                        >
                            <Download size={14} /> Export Chat
                        </button>
                    </div>
                </div>

                <div className="chat-messages" ref={messagesContainerRef}>
                    {messages.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">
                                <Bot size={24} />
                            </div>
                            <h3>Welcome to {activeDomainMeta.name} Intelligence</h3>
                            <p style={{ color: '#6B7280', fontSize: '0.875rem', margin: '-0.5rem 0 0.5rem', fontStyle: 'italic' }}>
                                Powered by local AI · All {activeDomainMeta.name} data stays private on your machine
                            </p>
                            <p>Ask a question about your {activeDomainMeta.name} datasets to get deterministic answers.</p>
                            <SuggestionChips
                                chips={activeDomainMeta.suggestedQueries}
                                onSelect={handleSuggestionClick}
                            />
                        </div>
                    ) : (
                        messages.map((msg, index) => (
                            <MessageBubble key={msg.id} msg={msg} isLatest={index === messages.length - 1} />
                        ))
                    )}

                    {isLoading && <TypingIndicator />}
                </div>

                <Composer
                    input={input}
                    setInput={setInput}
                    handleSend={handleSend}
                    isLoading={isLoading}
                    handleKeyDown={handleKeyDown}
                    availableFiles={availableFiles}
                    selectedFileId={selectedFileId}
                    onSelectFile={setSelectedFileId}
                />
            </div>

            {/* Scroll-down cue */}
            <div className="history-scroll-cue" onClick={scrollToHistory}>
                <div className="history-scroll-cue-content">
                    <History size={15} />
                    <span>Scroll down to view all saved conversations</span>
                    <ArrowDown size={14} className="bounce-arrow" />
                </div>
            </div>

            {/* Dedicated Past Conversations Section below the Chatbot */}
            <ChatHistorySection
                sessions={sessions}
                activeSessionId={sessionId}
                currentDomain={user.domain}
                onResumeSession={handleResumeSession}
                onDeleteSession={handleDeleteSession}
                onClearAllSessions={handleClearAllSessions}
                isLoadingSessions={isLoadingSessions}
            />
        </div>
    );
}
