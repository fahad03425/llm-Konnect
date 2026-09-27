import React, { useState, useEffect, useRef } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
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

function findMatchingFileIds(
    files: ScopeFile[],
    target: {
        fileId?: string;
        filePath?: string;
        fileName?: string;
        tableName?: string;
        groupName?: string;
        dbName?: string;
    }
): string[] {
    if (!files || files.length === 0) return [];

    // 1. Direct fileId match
    if (target.fileId) {
        const found = files.find(f => f.file_id === target.fileId);
        if (found) return [found.file_id];
    }

    // 2. Entire Database group match
    if (target.dbName) {
        const normDb = target.dbName.trim().toLowerCase();
        const dbMatches = files.filter(f =>
            (f.group_name && f.group_name.trim().toLowerCase() === normDb) ||
            (f.file_path && (
                f.file_path.toLowerCase().includes(`db://${normDb}`) ||
                f.file_path.toLowerCase().includes(`sql://${normDb}`)
            ))
        );
        if (dbMatches.length > 0) {
            return dbMatches.map(f => f.file_id);
        }
    }

    // 3. Database table match (groupName + tableName)
    if (target.groupName && target.tableName) {
        const normGrp = target.groupName.trim().toLowerCase();
        const normTbl = target.tableName.trim().toLowerCase();
        const tableMatch = files.find(f =>
            f.group_name && f.group_name.trim().toLowerCase() === normGrp &&
            (
                (f.table_name && f.table_name.trim().toLowerCase() === normTbl) ||
                f.filename.trim().toLowerCase() === normTbl
            )
        );
        if (tableMatch) return [tableMatch.file_id];
    }

    // 4. File Path match
    if (target.filePath) {
        const rawFp = target.filePath.trim();
        // Check if db://
        if (rawFp.startsWith('db://')) {
            const dbName = rawFp.replace('db://', '').trim().toLowerCase();
            const dbMatches = files.filter(f => f.group_name && f.group_name.trim().toLowerCase() === dbName);
            if (dbMatches.length > 0) return dbMatches.map(f => f.file_id);
        }

        // Check if sql://
        if (rawFp.startsWith('sql://')) {
            const pathParts = rawFp.replace('sql://', '').split('/');
            const dbPart = pathParts[0]?.toLowerCase();
            const tblPart = pathParts[1]?.toLowerCase();
            const sqlMatch = files.find(f => {
                if (f.file_path && f.file_path.toLowerCase() === rawFp.toLowerCase()) return true;
                if (dbPart && tblPart) {
                    return f.group_name?.toLowerCase() === dbPart &&
                        (f.table_name?.toLowerCase() === tblPart || f.filename.toLowerCase() === tblPart);
                }
                return false;
            });
            if (sqlMatch) return [sqlMatch.file_id];
        }

        // Normal file path matching (normalized slashes, case-insensitive)
        const normTarget = rawFp.replace(/\\/g, '/').toLowerCase();
        const baseTarget = normTarget.split('/').pop() || '';

        const pathMatch = files.find(f => {
            if (!f.file_path) return false;
            const normFp = f.file_path.replace(/\\/g, '/').toLowerCase();
            return normFp === normTarget || (baseTarget && normFp.endsWith('/' + baseTarget));
        });
        if (pathMatch) return [pathMatch.file_id];

        // Also check by filename matching baseTarget
        if (baseTarget) {
            const nameFromPathMatch = files.find(f => f.filename.toLowerCase() === baseTarget);
            if (nameFromPathMatch) return [nameFromPathMatch.file_id];
        }
    }

    // 5. File name match
    if (target.fileName) {
        const normName = target.fileName.trim().toLowerCase();
        const nameMatch = files.find(f =>
            f.filename.trim().toLowerCase() === normName ||
            (f.table_name && f.table_name.trim().toLowerCase() === normName)
        );
        if (nameMatch) return [nameMatch.file_id];
    }

    // 6. Table name only match
    if (target.tableName) {
        const normTbl = target.tableName.trim().toLowerCase();
        const tblMatch = files.find(f =>
            (f.table_name && f.table_name.trim().toLowerCase() === normTbl) ||
            f.filename.trim().toLowerCase() === normTbl
        );
        if (tblMatch) return [tblMatch.file_id];
    }

    return [];
}

export default function Chatbot() {
    const { user, activeDomainMeta, setDomain } = useUser();
    const location = useLocation();
    const [searchParams] = useSearchParams();
    const lastAppliedNavKey = useRef<string | null>(null);
    const composerInputRef = useRef<HTMLInputElement>(null);

    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [availableFiles, setAvailableFiles] = useState<ScopeFile[]>([]);
    const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
    const [sessionId, setSessionId] = useState(() => `sess-${Math.random().toString(36).substring(2, 10)}`);
    const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);
    const [isLoadingSessions, setIsLoadingSessions] = useState(false);
    const [models, setModels] = useState<string[]>(['qwen2.5:3b', 'gemma3:1b', 'llama3.2:1b', 'llama3.2:3b']);
    const [activeModel, setActiveModel] = useState<string>('qwen2.5:3b');

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
                    chunk_count: f.chunk_count,
                    source_type: f.source_type,
                    group_name: f.group_name,
                    table_name: f.table_name,
                    file_path: f.file_path,
                    domain: f.domain
                }));
                setAvailableFiles(files);
                return files;
            }
        } catch (e) {
            console.error('Could not load sources for chat scoping', e);
        }
        return [];
    };

    const fetchModels = async () => {
        try {
            const res = await fetch('/api/chat/models');
            if (res.ok) {
                const data = await res.json();
                if (data.models && data.models.length > 0) {
                    setModels(data.models);
                }
                if (data.active_model) {
                    setActiveModel(data.active_model);
                }
            }
        } catch (e) {
            console.error('Could not fetch installed models', e);
        }
    };

    const handleModelChange = async (e: React.ChangeEvent<HTMLSelectElement>) => {
        const newModel = e.target.value;
        setActiveModel(newModel);
        try {
            await fetch('/api/chat/models/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: newModel })
            });
        } catch (err) {
            console.error('Failed to update active model', err);
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
                    domain: user.domain,
                    selected_file_ids: selectedFileIds
                })
            });
            fetchSessions();
        } catch (e) {
            console.error('Error auto-saving session', e);
        }
    };

    useEffect(() => {
        document.title = `${activeDomainMeta.name} RAG Chatbot — LLM-KONNECT`;
        fetchModels();
    }, [activeDomainMeta.name]);

    useEffect(() => {
        fetchSources();
    }, [user.domain]);

    useEffect(() => {
        scrollToBottom();
    }, [messages, isLoading]);

    useEffect(() => {
        fetchSessions();
    }, [user.domain]);

    // Handle navigation from "Chat" or "Start Chatting" button
    useEffect(() => {
        const state = (location.state as any) || {};
        const urlFileId = searchParams.get('fileId') || searchParams.get('file_id');
        const urlFilePath = searchParams.get('filePath') || searchParams.get('file_path');
        const urlFileName = searchParams.get('fileName') || searchParams.get('file_name');
        const urlDb = searchParams.get('db') || searchParams.get('database');
        const urlTable = searchParams.get('table') || searchParams.get('table_name');
        const urlDomain = searchParams.get('domain');

        const navTarget = {
            fileId: state.fileId || urlFileId,
            filePath: state.filePath || urlFilePath,
            fileName: state.fileName || urlFileName,
            tableName: state.tableName || urlTable,
            groupName: state.groupName,
            dbName: state.dbName || urlDb,
            domain: state.domain || urlDomain
        };

        const hasTarget = Boolean(
            navTarget.fileId || navTarget.filePath || navTarget.fileName || 
            navTarget.tableName || navTarget.dbName
        );

        if (!hasTarget) return;

        // If domain differs, switch domain so sources can be fetched for that domain
        if (navTarget.domain && navTarget.domain !== user.domain) {
            setDomain(navTarget.domain as any);
        }

        // Only apply if this navigation key hasn't been applied yet
        if (lastAppliedNavKey.current === location.key) return;

        if (availableFiles.length > 0) {
            const matched = findMatchingFileIds(availableFiles, navTarget);
            if (matched.length > 0) {
                setSelectedFileIds(matched);
                lastAppliedNavKey.current = location.key;
                // Start a fresh session for this scoped chat
                const newId = `sess-${Math.random().toString(36).substring(2, 10)}`;
                setSessionId(newId);
                setMessages([]);
                setInput('');
                // Focus composer input directly
                setTimeout(() => {
                    composerInputRef.current?.focus();
                }, 100);
            }
        }
    }, [location.key, location.state, searchParams, availableFiles, user.domain, setDomain]);

    const handleNewChat = () => {
        const newId = `sess-${Math.random().toString(36).substring(2, 10)}`;
        setSessionId(newId);
        setMessages([]);
        setInput('');
        setSelectedFileIds([]);
        lastAppliedNavKey.current = location.key;
        scrollToTop();
        composerInputRef.current?.focus();
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
                if (data.selected_file_ids && Array.isArray(data.selected_file_ids)) {
                    setSelectedFileIds(data.selected_file_ids);
                } else {
                    setSelectedFileIds([]);
                }
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
        const startTime = Date.now();

        try {
            const payload: any = {
                question: userMsg,
                session_id: sessionId,
                domain: user.domain
            };
            if (selectedFileIds.length > 0) {
                payload.file_ids = selectedFileIds;
            }

            const res = await fetch('/api/chat/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (res.ok && res.body) {
                const reader = res.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let fullAnswer = '';
                let route = 'rag';
                let sources: any[] = [];
                let buffer = '';
                let hasStarted = false;

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';

                    for (const line of lines) {
                        const trimmed = line.trim();
                        if (!trimmed) continue;
                        try {
                            const parsed = JSON.parse(trimmed);
                            if (parsed.error) {
                                throw new Error(parsed.error);
                            }
                            if (parsed.chunk !== undefined) {
                                fullAnswer += parsed.chunk;
                            }
                            if (parsed.route) {
                                route = parsed.route;
                            }
                            if (parsed.sources && parsed.sources.length > 0) {
                                sources = parsed.sources;
                            }

                            if (!hasStarted) {
                                hasStarted = true;
                                setIsLoading(false);
                            }

                            const currentAssistantMsg: Message = {
                                id: botMsgId,
                                role: 'assistant',
                                content: fullAnswer,
                                route: route,
                                sources: sources,
                                timing: Number(((Date.now() - startTime) / 1000).toFixed(2)),
                                timestamp: new Date().toISOString()
                            };
                            setMessages([...updatedMsgs, currentAssistantMsg]);
                        } catch (e: any) {
                            if (e.message && !e.message.includes('JSON')) {
                                throw e;
                            }
                        }
                    }
                }

                if (buffer.trim()) {
                    try {
                        const parsed = JSON.parse(buffer.trim());
                        if (parsed.chunk) fullAnswer += parsed.chunk;
                        if (parsed.route) route = parsed.route;
                        if (parsed.sources) sources = parsed.sources;
                    } catch {}
                }

                const finalAssistantMsg: Message = {
                    id: botMsgId,
                    role: 'assistant',
                    content: fullAnswer || "No response received.",
                    route: route,
                    sources: sources,
                    timing: Number(((Date.now() - startTime) / 1000).toFixed(2)),
                    timestamp: new Date().toISOString()
                };
                const allFinalMsgs = [...updatedMsgs, finalAssistantMsg];
                setMessages(allFinalMsgs);
                saveCurrentSession(allFinalMsgs, sessionId);
            } else {
                // Non-streaming fallback
                const nonStreamRes = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });

                if (nonStreamRes.ok) {
                    const data = await nonStreamRes.json();
                    const assistantMsg: Message = {
                        id: botMsgId,
                        role: 'assistant',
                        content: data.answer,
                        route: data.route,
                        sources: data.sources || [],
                        timing: data.timing || Number(((Date.now() - startTime) / 1000).toFixed(2)),
                        timestamp: new Date().toISOString()
                    };
                    const allFinalMsgs = [...updatedMsgs, assistantMsg];
                    setMessages(allFinalMsgs);
                    saveCurrentSession(allFinalMsgs, sessionId);
                } else {
                    const errData = await nonStreamRes.json().catch(() => ({ detail: `Error ${nonStreamRes.status}` }));
                    throw new Error(errData.detail || `Server returned ${nonStreamRes.status}`);
                }
            }
        } catch (err: any) {
            console.error('Chat request failed:', err);
            const errorMsg: Message = {
                id: Date.now().toString() + 'err',
                role: 'error',
                content: "Local AI engine encountered an issue. Please make sure Ollama and the backend are running.",
                timestamp: new Date().toISOString()
            };
            const allFinalMsgs = [...updatedMsgs, errorMsg];
            setMessages(allFinalMsgs);
            saveCurrentSession(allFinalMsgs, sessionId);
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
                        <select 
                            className="model-select-dropdown"
                            value={activeModel}
                            onChange={handleModelChange}
                            title="Switch local AI model for speed or depth"
                        >
                            {models.map(m => (
                                <option key={m} value={m}>
                                    {m.includes('gemma3') ? `⚡ ${m} (Ultra Fast 1B)` : m.includes('qwen2.5') ? `🎯 ${m} (Fast & Accurate 3B)` : m.includes('qwen3') ? `🧠 ${m} (Reasoning 4B)` : `🤖 ${m}`}
                                </option>
                            ))}
                        </select>
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
                    selectedFileIds={selectedFileIds}
                    onSelectFiles={setSelectedFileIds}
                    inputRef={composerInputRef}
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
