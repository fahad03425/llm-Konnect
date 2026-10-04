import React, { useEffect, useRef } from 'react';
import { useLocation, useSearchParams } from 'react-router-dom';
import { Bot, Download, Plus, History, ArrowDown, RefreshCw } from 'lucide-react';
import { MessageBubble } from '../components/chat/MessageBubble';
import { TypingIndicator } from '../components/chat/TypingIndicator';
import { SuggestionChips } from '../components/chat/SuggestionChips';
import { Composer } from '../components/chat/Composer';
import { ChatHistorySection } from '../components/chat/ChatHistorySection';
import { useUser } from '../context/UserContext';
import { useChat, findMatchingFileIds } from '../context/ChatContext';
import '../Chat.css';

export default function Chatbot() {
    const { user, activeDomainMeta, setDomain } = useUser();
    const {
        messages,
        input,
        setInput,
        isLoading,
        isGenerating,
        availableFiles,
        selectedFileIds,
        setSelectedFileIds,
        sessionId,
        sessions,
        isLoadingSessions,
        models,
        activeModel,
        handleModelChange,
        handleNewChat,
        handleResumeSession,
        handleDeleteSession,
        handleClearAllSessions,
        handleExportChat,
        handleSend,
        handleStopGeneration,
        handleRefreshCurrentChat,
        fetchSources
    } = useChat();

    // Only show typing indicator for the session that is actually generating
    // and hasn't yet received the first assistant response chunk
    const lastMessage = messages[messages.length - 1];
    const showTypingIndicator = isLoading && (!lastMessage || lastMessage.role === 'user');

    const location = useLocation();
    const [searchParams] = useSearchParams();
    const lastAppliedNavKey = useRef<string | null>(null);
    const composerInputRef = useRef<HTMLInputElement>(null);

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

    useEffect(() => {
        document.title = `${activeDomainMeta.name} RAG Chatbot — LLM-KONNECT`;
        fetchSources();
    }, [activeDomainMeta.name]);

    useEffect(() => {
        scrollToBottom();
    }, [messages, showTypingIndicator]);

    // Handle navigation from "Chat" or "Start Chatting" button from UploadedFiles or ConnectSource
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
                lastAppliedNavKey.current = location.key;
                // Start a fresh session with the scoped files
                handleNewChat(matched);
                // Focus composer input directly
                setTimeout(() => {
                    composerInputRef.current?.focus();
                }, 100);
            }
        }
    }, [location.key, location.state, searchParams, availableFiles, user.domain, setDomain, handleNewChat]);

    const onNewChatClick = () => {
        handleNewChat([]);
        lastAppliedNavKey.current = location.key;
        scrollToTop();
        composerInputRef.current?.focus();
    };

    const onResumeSessionClick = async (resumeId: string) => {
        await handleResumeSession(resumeId);
        scrollToTop();
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
                        <select 
                            className="model-select-dropdown"
                            value={activeModel}
                            onChange={handleModelChange}
                            title="Switch local AI model"
                        >
                            {models.map(m => {
                                let label = m;
                                if (m.includes('1.5b')) label = `${m} (Fast & Accurate 1.5B)`;
                                else if (m.includes('0.5b')) label = `${m} (Ultra Fast 0.5B)`;
                                else if (m.includes('llama3.2:1b')) label = `${m} (Lightweight 1.2B)`;
                                else if (m.includes('gemma3')) label = `${m} (Fast 1B)`;
                                else if (m.includes('qwen2.5') && m.includes('3b')) label = `${m} (Multilingual 3B)`;
                                else if (m.includes('llama3.2') && m.includes('3b')) label = `${m} (Balanced 3B)`;
                                else if (m.includes('phi4')) label = `${m} (CPU 3.8B)`;
                                else if (m.includes('phi3')) label = `${m} (CPU 3.8B)`;
                                return (
                                    <option key={m} value={m}>
                                        {label}
                                    </option>
                                );
                            })}
                        </select>
                    </div>
                    <div className="chat-actions">
                        {isGenerating ? (
                            <button 
                                className="action-btn btn-stop-generation"
                                onClick={() => handleStopGeneration()}
                                title="Stop generation and refresh chat"
                            >
                                <RefreshCw size={14} className="animate-spin" /> Stop Generation
                            </button>
                        ) : (
                            <button 
                                className="action-btn" 
                                onClick={handleRefreshCurrentChat}
                                title="Refresh current conversation"
                            >
                                <RefreshCw size={14} /> Refresh
                            </button>
                        )}
                        <button 
                            className="btn-new-chat-primary"
                            onClick={onNewChatClick}
                            title="Start a fresh conversation"
                        >
                            <Plus size={14} /> New Chat
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
                            <h3>Query your knowledge base</h3>
                            <p>Ask questions across your connected datasets and documents with local AI.</p>
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

                    {showTypingIndicator && <TypingIndicator onStop={handleStopGeneration} />}
                </div>

                <Composer
                    input={input}
                    setInput={setInput}
                    handleSend={handleSend}
                    handleStopGeneration={handleStopGeneration}
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
                onResumeSession={onResumeSessionClick}
                onDeleteSession={handleDeleteSession}
                onClearAllSessions={handleClearAllSessions}
                isLoadingSessions={isLoadingSessions}
            />
        </div>
    );
}
