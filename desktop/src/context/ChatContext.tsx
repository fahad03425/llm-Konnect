import React, { createContext, useContext, useState, useEffect, useRef, type ReactNode } from 'react';
import { useUser } from './UserContext';

export interface Message {
    id: string;
    role: 'user' | 'assistant' | 'error';
    content: string;
    route?: string;
    sources?: { source_file: string; source_row: number; label: string }[];
    timing?: number;
    timestamp?: string;
}

export interface ScopeFile {
    file_id: string;
    filename: string;
    chunk_count: number;
    source_type?: string;
    group_name?: string;
    table_name?: string;
    file_path?: string;
    domain?: string;
}

export interface ChatSessionMeta {
    id: string;
    title: string;
    domain?: string;
    created_at: string;
    updated_at: string;
    message_count: number;
    last_message: string;
}

export function findMatchingFileIds(
    files: ScopeFile[],
    target: {
        fileId?: string;
        filePath?: string;
        fileName?: string;
        tableName?: string;
        groupName?: string;
        dbName?: string;
        dbNames?: string[];
    }
): string[] {
    if (!files || files.length === 0) return [];

    // 1. Direct fileId match
    if (target.fileId) {
        const found = files.find(f => f.file_id === target.fileId);
        if (found) return [found.file_id];
    }

    // 2. Multiple DBs array match
    if (target.dbNames && target.dbNames.length > 0) {
        const normDbs = target.dbNames.map(d => d.trim().toLowerCase());
        const dbMatches = files.filter(f => f.group_name && normDbs.includes(f.group_name.trim().toLowerCase()));
        if (dbMatches.length > 0) {
            return dbMatches.map(f => f.file_id);
        }
    }

    // 2b. Database group match (single or comma-separated list)
    if (target.dbName) {
        const cleanDb = target.dbName.replace('db://', '').trim().toLowerCase();
        const dbParts = cleanDb.split(',').map(s => s.trim()).filter(Boolean);
        if (dbParts.length > 0) {
            const dbMatches = files.filter(f =>
                f.group_name && dbParts.includes(f.group_name.trim().toLowerCase())
            );
            if (dbMatches.length > 0) {
                return dbMatches.map(f => f.file_id);
            }
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
            const cleanDb = rawFp.replace('db://', '').trim().toLowerCase();
            const dbParts = cleanDb.split(',').map(s => s.trim()).filter(Boolean);
            if (dbParts.length === 1 && dbParts[0] === 'all') {
                return files.filter(f => f.source_type === 'database' || f.group_name).map(f => f.file_id);
            }
            const dbMatches = files.filter(f => f.group_name && dbParts.includes(f.group_name.trim().toLowerCase()));
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

const ORDERED_PRESETS = [
    'qwen2.5:1.5b',  // Best speed + accuracy for low-end
    'qwen2.5:0.5b',  // Ultra fast, lowest RAM (< 400MB)
    'llama3.2:1b',   // Lightweight & fast (1.2B)
    'gemma3:1b',     // Fast (1B)
    'qwen2.5:3b',    // High accuracy & multilingual (3B)
    'llama3.2:3b'    // Accurate & balanced (3B)
];

const isReasoningModel = (name: string): boolean => {
    const lower = name.toLowerCase();
    return lower.includes('qwen3') || lower.includes('deepseek-r1') || lower.includes('qwq') || lower.includes('r1:');
};

interface ChatContextType {
    messages: Message[];
    setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
    input: string;
    setInput: (val: string) => void;
    isLoading: boolean;
    availableFiles: ScopeFile[];
    setAvailableFiles: React.Dispatch<React.SetStateAction<ScopeFile[]>>;
    selectedFileIds: string[];
    setSelectedFileIds: React.Dispatch<React.SetStateAction<string[]>>;
    sessionId: string;
    setSessionId: (id: string) => void;
    sessions: ChatSessionMeta[];
    isLoadingSessions: boolean;
    models: string[];
    activeModel: string;
    setActiveModel: (m: string) => void;
    fetchSources: () => Promise<ScopeFile[]>;
    fetchModels: () => Promise<void>;
    fetchSessions: () => Promise<void>;
    handleModelChange: (modelOrEvent: string | React.ChangeEvent<HTMLSelectElement>) => Promise<void>;
    saveCurrentSession: (currentMsgs: Message[], targetSessionId?: string) => Promise<void>;
    handleNewChat: (initialFileIds?: string[]) => void;
    handleResumeSession: (resumeId: string) => Promise<void>;
    handleDeleteSession: (delId: string) => Promise<void>;
    handleClearAllSessions: () => Promise<void>;
    handleExportChat: () => void;
    handleSend: (text?: string) => Promise<void>;
}

const ChatContext = createContext<ChatContextType | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
    const { user } = useUser();

    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [availableFiles, setAvailableFiles] = useState<ScopeFile[]>([]);
    const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
    const [sessionId, setSessionId] = useState<string>(() => `sess-${Math.random().toString(36).substring(2, 10)}`);
    const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);
    const [isLoadingSessions, setIsLoadingSessions] = useState(false);
    const [models, setModels] = useState<string[]>(ORDERED_PRESETS);
    const [activeModel, setActiveModel] = useState<string>('qwen2.5:1.5b');

    // Keep active domain ref to prevent race conditions during domain switch
    const currentDomainRef = useRef(user.domain);
    useEffect(() => {
        currentDomainRef.current = user.domain;
    }, [user.domain]);

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
                const backendModels = (data.models || []).filter((m: string) => !isReasoningModel(m));
                const merged = Array.from(new Set([...ORDERED_PRESETS, ...backendModels]));
                const sorted = merged.sort((a, b) => {
                    const idxA = ORDERED_PRESETS.indexOf(a);
                    const idxB = ORDERED_PRESETS.indexOf(b);
                    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                    if (idxA !== -1) return -1;
                    if (idxB !== -1) return 1;
                    return a.localeCompare(b);
                });
                setModels(sorted);
                if (data.active_model && !isReasoningModel(data.active_model)) {
                    setActiveModel(data.active_model);
                } else {
                    setActiveModel('qwen2.5:1.5b');
                    fetch('/api/chat/models/select', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ model: 'qwen2.5:1.5b' })
                    }).catch(() => {});
                }
            }
        } catch (e) {
            console.error('Could not fetch installed models', e);
        }
    };

    const handleModelChange = async (modelOrEvent: string | React.ChangeEvent<HTMLSelectElement>) => {
        const newModel = typeof modelOrEvent === 'string' ? modelOrEvent : modelOrEvent.target.value;
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
            const res = await fetch(`/api/chat/sessions?domain=${encodeURIComponent(user.domain)}`);
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

    const handleNewChat = (initialFileIds: string[] = []) => {
        const newId = `sess-${Math.random().toString(36).substring(2, 10)}`;
        setSessionId(newId);
        setMessages([]);
        setInput('');
        setSelectedFileIds(initialFileIds);
    };

    const handleResumeSession = async (resumeId: string) => {
        if (resumeId === sessionId && messages.length > 0) {
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

        const currentSessionForRequest = sessionId;
        const botMsgId = Date.now().toString() + 'bot';
        const startTime = Date.now();

        try {
            const payload: any = {
                question: userMsg,
                session_id: currentSessionForRequest,
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
                            if (parsed.active_model) {
                                setActiveModel(parsed.active_model);
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
                saveCurrentSession(allFinalMsgs, currentSessionForRequest);
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
                    saveCurrentSession(allFinalMsgs, currentSessionForRequest);
                } else {
                    const errData = await nonStreamRes.json().catch(() => ({ detail: `Error ${nonStreamRes.status}` }));
                    throw new Error(errData.detail || `Server returned ${nonStreamRes.status}`);
                }
            }
        } catch (err: any) {
            const errorDetail = err?.message && !err.message.includes('object Object')
                ? err.message
                : "Local AI engine encountered an issue. Please make sure Ollama and the backend are running.";
            const errorMsg: Message = {
                id: Date.now().toString() + 'err',
                role: 'error',
                content: errorDetail,
                timestamp: new Date().toISOString()
            };
            const allFinalMsgs = [...updatedMsgs, errorMsg];
            setMessages(allFinalMsgs);
            saveCurrentSession(allFinalMsgs, currentSessionForRequest);
        } finally {
            setIsLoading(false);
            fetchSessions();
        }
    };

    // Initialize models and domain data on mount and domain change
    useEffect(() => {
        fetchModels();
    }, []);

    useEffect(() => {
        fetchSources();
        fetchSessions();
    }, [user.domain]);

    return (
        <ChatContext.Provider
            value={{
                messages,
                setMessages,
                input,
                setInput,
                isLoading,
                availableFiles,
                setAvailableFiles,
                selectedFileIds,
                setSelectedFileIds,
                sessionId,
                setSessionId,
                sessions,
                isLoadingSessions,
                models,
                activeModel,
                setActiveModel,
                fetchSources,
                fetchModels,
                fetchSessions,
                handleModelChange,
                saveCurrentSession,
                handleNewChat,
                handleResumeSession,
                handleDeleteSession,
                handleClearAllSessions,
                handleExportChat,
                handleSend
            }}
        >
            {children}
        </ChatContext.Provider>
    );
}

export function useChat() {
    const ctx = useContext(ChatContext);
    if (!ctx) {
        throw new Error('useChat must be used within a ChatProvider');
    }
    return ctx;
}
