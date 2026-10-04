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
    isGenerating: boolean;
    generatingSessionId: string | null;
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
    handleStopGeneration: (targetSessionId?: string) => void;
    handleRefreshCurrentChat: () => Promise<void>;
}

const ChatContext = createContext<ChatContextType | null>(null);

export function ChatProvider({ children }: { children: ReactNode }) {
    const { user } = useUser();

    const [messages, setMessages] = useState<Message[]>([]);
    const [input, setInput] = useState('');
    const [generatingSessionId, setGeneratingSessionId] = useState<string | null>(null);
    const [availableFiles, setAvailableFiles] = useState<ScopeFile[]>([]);
    const [selectedFileIds, setSelectedFileIds] = useState<string[]>([]);
    const [sessionId, setSessionId] = useState<string>(() => `sess-${Math.random().toString(36).substring(2, 10)}`);
    const [sessions, setSessions] = useState<ChatSessionMeta[]>([]);
    const [isLoadingSessions, setIsLoadingSessions] = useState(false);
    const [models, setModels] = useState<string[]>(ORDERED_PRESETS);
    const [activeModel, setActiveModel] = useState<string>('qwen2.5:1.5b');

    // References to keep active state accessible in async callbacks and race-free
    const activeSessionIdRef = useRef<string>(sessionId);
    useEffect(() => {
        activeSessionIdRef.current = sessionId;
    }, [sessionId]);

    const generatingSessionIdRef = useRef<string | null>(null);
    useEffect(() => {
        generatingSessionIdRef.current = generatingSessionId;
    }, [generatingSessionId]);

    const abortControllerRef = useRef<AbortController | null>(null);
    const sessionMessagesCacheRef = useRef<Record<string, Message[]>>({});

    // isLoading is true ONLY if the current active sessionId is generating
    const isLoading = Boolean(generatingSessionId && generatingSessionId === sessionId);
    // isGenerating is true if ANY chat session is generating in background
    const isGenerating = Boolean(generatingSessionId);

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
                const merged = Array.from(new Set<string>(backendModels));
                const sorted = merged.sort((a, b) => {
                    const idxA = ORDERED_PRESETS.indexOf(a);
                    const idxB = ORDERED_PRESETS.indexOf(b);
                    if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                    if (idxA !== -1) return -1;
                    if (idxB !== -1) return 1;
                    return a.localeCompare(b);
                });
                setModels(sorted);
                if (data.active_model) setActiveModel(data.active_model);
            }
        } catch (e) {
            console.error('Could not fetch installed models', e);
        }
    };

    const handleModelChange = async (modelOrEvent: string | React.ChangeEvent<HTMLSelectElement>) => {
        const newModel = typeof modelOrEvent === 'string' ? modelOrEvent : modelOrEvent.target.value;
        try {
            const response = await fetch('/api/chat/models/select', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ model: newModel })
            });
            if (!response.ok) {
                const body = await response.json().catch(() => null);
                throw new Error(body?.detail || 'Failed to update active model');
            }
            const result = await response.json();
            setActiveModel(result.active_model);
        } catch (err) {
            console.error('Failed to update active model', err);
            await fetchModels();
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
        if (messages.length > 0) {
            saveCurrentSession(messages, sessionId);
        }
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

        if (messages.length > 0) {
            saveCurrentSession(messages, sessionId);
        }

        try {
            const res = await fetch(`/api/chat/sessions/${resumeId}`);
            if (res.ok) {
                const data = await res.json();
                setSessionId(resumeId);
                // If this session is actively generating in background and has cached messages:
                if (generatingSessionIdRef.current === resumeId && sessionMessagesCacheRef.current[resumeId]) {
                    setMessages(sessionMessagesCacheRef.current[resumeId]);
                } else {
                    const loadedMsgs = data.messages || [];
                    setMessages(loadedMsgs);
                    sessionMessagesCacheRef.current[resumeId] = loadedMsgs;
                }
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

    const handleStopGeneration = (targetSessionId?: string) => {
        const sessIdToStop = targetSessionId || generatingSessionIdRef.current || sessionId;

        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
        }

        setGeneratingSessionId(null);
        generatingSessionIdRef.current = null;

        const currentActive = activeSessionIdRef.current;
        if (currentActive === sessIdToStop) {
            setMessages(prev => {
                const lastMsg = prev[prev.length - 1];
                let finalMsgs = prev;
                if (lastMsg && lastMsg.role === 'assistant') {
                    if (!lastMsg.content.trim()) {
                        // Empty assistant bubble before any text generated; remove it, keeping user question intact!
                        finalMsgs = prev.slice(0, -1);
                    } else if (!lastMsg.content.includes('(Generation stopped)')) {
                        finalMsgs = [
                            ...prev.slice(0, -1),
                            {
                                ...lastMsg,
                                content: lastMsg.content.trim() + '\n\n*(Generation stopped)*'
                            }
                        ];
                    }
                }
                sessionMessagesCacheRef.current[sessIdToStop] = finalMsgs;
                saveCurrentSession(finalMsgs, sessIdToStop);
                return finalMsgs;
            });
        } else {
            const cached = sessionMessagesCacheRef.current[sessIdToStop];
            if (cached && cached.length > 0) {
                const lastMsg = cached[cached.length - 1];
                let finalMsgs = cached;
                if (lastMsg && lastMsg.role === 'assistant') {
                    if (!lastMsg.content.trim()) {
                        finalMsgs = cached.slice(0, -1);
                    } else if (!lastMsg.content.includes('(Generation stopped)')) {
                        finalMsgs = [
                            ...cached.slice(0, -1),
                            {
                                ...lastMsg,
                                content: lastMsg.content.trim() + '\n\n*(Generation stopped)*'
                            }
                        ];
                    }
                }
                sessionMessagesCacheRef.current[sessIdToStop] = finalMsgs;
                saveCurrentSession(finalMsgs, sessIdToStop);
            }
        }

        fetchSessions();
    };

    const handleRefreshCurrentChat = async () => {
        if (generatingSessionIdRef.current) {
            handleStopGeneration();
            return;
        }

        try {
            await fetchSources();
            if (sessionId) {
                const res = await fetch(`/api/chat/sessions/${sessionId}`);
                if (res.ok) {
                    const data = await res.json();
                    if (data.messages && Array.isArray(data.messages)) {
                        setMessages(data.messages);
                        sessionMessagesCacheRef.current[sessionId] = data.messages;
                    }
                }
            }
            await fetchSessions();
        } catch (e) {
            console.error('Error refreshing chat session', e);
        }
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

        const currentSessionForRequest = sessionId;
        setInput('');
        const updatedMsgs = [...messages, userMsgObj];
        setMessages(updatedMsgs);
        sessionMessagesCacheRef.current[currentSessionForRequest] = updatedMsgs;

        // CRITICAL: Immediately persist the user question so it will NEVER disappear
        // even if cancelled before assistant responds or user navigates/refreshes.
        saveCurrentSession(updatedMsgs, currentSessionForRequest);

        // Abort previous request if still running
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }
        const controller = new AbortController();
        abortControllerRef.current = controller;

        setGeneratingSessionId(currentSessionForRequest);
        generatingSessionIdRef.current = currentSessionForRequest;

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
                body: JSON.stringify(payload),
                signal: controller.signal
            });

            if (res.ok && res.body) {
                const reader = res.body.getReader();
                const decoder = new TextDecoder('utf-8');
                let fullAnswer = '';
                let route = 'rag';
                let sources: any[] = [];
                let buffer = '';

                const consumeFrame = (raw: string) => {
                    const trimmed = raw.trim();
                    if (!trimmed) return;

                    let parsed: any;
                    try {
                        parsed = JSON.parse(trimmed);
                    } catch {
                        throw new Error('The backend sent an invalid streaming response. Please retry.');
                    }
                    if (parsed.error) {
                        throw new Error(String(parsed.error));
                    }
                    if (parsed.chunk !== undefined) fullAnswer += String(parsed.chunk);
                    if (parsed.route) route = parsed.route;
                    if (Array.isArray(parsed.sources)) sources = parsed.sources;
                    if (parsed.active_model) setActiveModel(parsed.active_model);

                    const currentAssistantMsg: Message = {
                        id: botMsgId,
                        role: 'assistant',
                        content: fullAnswer,
                        route,
                        sources,
                        timing: Number(((Date.now() - startTime) / 1000).toFixed(2)),
                        timestamp: new Date().toISOString()
                    };
                    const currentMsgs = [...updatedMsgs, currentAssistantMsg];
                    sessionMessagesCacheRef.current[currentSessionForRequest] = currentMsgs;
                    if (activeSessionIdRef.current === currentSessionForRequest) {
                        setMessages(currentMsgs);
                    }
                };

                while (true) {
                    if (controller.signal.aborted) {
                        try { await reader.cancel(); } catch {}
                        break;
                    }

                    const { done, value } = await reader.read();
                    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
                    if (done) break;

                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) consumeFrame(line);
                }

                if (controller.signal.aborted) return;
                consumeFrame(buffer);
                if (!fullAnswer.trim()) {
                    throw new Error('The backend closed the stream without returning an answer. Please retry.');
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
                sessionMessagesCacheRef.current[currentSessionForRequest] = allFinalMsgs;

                if (activeSessionIdRef.current === currentSessionForRequest) {
                    setMessages(allFinalMsgs);
                }
                saveCurrentSession(allFinalMsgs, currentSessionForRequest);
            } else if (!res.ok) {
                const body = await res.json().catch(() => ({}));
                throw new Error(body.detail || body.error || `Chat server returned ${res.status}.`);
            } else {
                // Older browsers or proxies may not expose a response stream.
                const nonStreamRes = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload),
                    signal: controller.signal
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
                    sessionMessagesCacheRef.current[currentSessionForRequest] = allFinalMsgs;

                    if (activeSessionIdRef.current === currentSessionForRequest) {
                        setMessages(allFinalMsgs);
                    }
                    saveCurrentSession(allFinalMsgs, currentSessionForRequest);
                } else {
                    const errData = await nonStreamRes.json().catch(() => ({ detail: `Error ${nonStreamRes.status}` }));
                    throw new Error(errData.detail || `Server returned ${nonStreamRes.status}`);
                }
            }
        } catch (err: any) {
            if (err?.name === 'AbortError' || err?.message?.includes('aborted') || controller.signal.aborted) {
                // Stopped intentionally by user - do not display error message bubble
                return;
            }

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
            sessionMessagesCacheRef.current[currentSessionForRequest] = allFinalMsgs;

            if (activeSessionIdRef.current === currentSessionForRequest) {
                setMessages(allFinalMsgs);
            }
            saveCurrentSession(allFinalMsgs, currentSessionForRequest);
        } finally {
            if (generatingSessionIdRef.current === currentSessionForRequest) {
                setGeneratingSessionId(null);
                generatingSessionIdRef.current = null;
            }
            if (abortControllerRef.current === controller) {
                abortControllerRef.current = null;
            }
            fetchSessions();
        }
    };

    // Initialize models and domain data on mount and domain change
    useEffect(() => {
        fetchModels();
        window.addEventListener('models:updated', fetchModels);
        return () => window.removeEventListener('models:updated', fetchModels);
    }, []);

    useEffect(() => {
        fetchSources();
        fetchSessions();

        // Auto-refresh when window gains focus or custom event fires
        const handleFocus = () => {
            fetchSources();
            fetchSessions();
        };

        const handleSourcesUpdated = () => {
            fetchSources();
        };

        window.addEventListener('focus', handleFocus);
        window.addEventListener('kb:sources-updated', handleSourcesUpdated);

        // Lightweight background poll (every 8 seconds) to detect newly ingested files
        const pollInterval = setInterval(() => {
            fetchSources();
        }, 8000);

        return () => {
            window.removeEventListener('focus', handleFocus);
            window.removeEventListener('kb:sources-updated', handleSourcesUpdated);
            clearInterval(pollInterval);
        };
    }, [user.domain]);

    return (
        <ChatContext.Provider
            value={{
                messages,
                setMessages,
                input,
                setInput,
                isLoading,
                isGenerating,
                generatingSessionId,
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
                handleSend,
                handleStopGeneration,
                handleRefreshCurrentChat
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
