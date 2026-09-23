import React, { useState } from 'react';
import { 
    MessageSquare, 
    Trash2, 
    Clock, 
    Search, 
    Filter, 
    Sparkles, 
    ChevronRight,
    AlertTriangle
} from 'lucide-react';

export interface ChatSessionMeta {
    id: string;
    title: string;
    domain: string;
    created_at: string;
    updated_at: string;
    message_count: number;
    last_message: string;
}

interface ChatHistorySectionProps {
    sessions: ChatSessionMeta[];
    activeSessionId: string;
    currentDomain: string;
    onResumeSession: (sessionId: string) => void;
    onDeleteSession: (sessionId: string) => void;
    onClearAllSessions: () => void;
    isLoadingSessions?: boolean;
}

export const ChatHistorySection: React.FC<ChatHistorySectionProps> = ({
    sessions,
    activeSessionId,
    currentDomain,
    onResumeSession,
    onDeleteSession,
    onClearAllSessions,
    isLoadingSessions = false
}) => {
    const [searchQuery, setSearchQuery] = useState('');
    const [domainFilter, setDomainFilter] = useState<'all' | 'current'>('all');
    const [confirmClear, setConfirmClear] = useState(false);

    const formatTime = (isoString: string) => {
        try {
            const date = new Date(isoString);
            const now = new Date();
            const diffMs = now.getTime() - date.getTime();
            const diffMins = Math.floor(diffMs / (1000 * 60));
            const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
            const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

            if (diffMins < 1) return 'Just now';
            if (diffMins < 60) return `${diffMins}m ago`;
            if (diffHours < 24) return `${diffHours}h ago`;
            if (diffDays === 1) return 'Yesterday';
            if (diffDays < 7) return `${diffDays}d ago`;
            return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined });
        } catch {
            return 'Recently';
        }
    };

    const getDomainBadge = (dom: string) => {
        switch (dom?.toLowerCase()) {
            case 'pharmacy':
                return { label: '💊 Pharmacy & Health', color: '#0D7377', bg: 'rgba(13, 115, 119, 0.1)' };
            case 'retail':
                return { label: '🛒 Retail & E-Commerce', color: '#D97706', bg: 'rgba(217, 119, 6, 0.1)' };
            case 'finance':
                return { label: '💳 Financial Services', color: '#2563EB', bg: 'rgba(37, 99, 235, 0.1)' };
            case 'fmcg':
                return { label: '📦 FMCG & CPG', color: '#059669', bg: 'rgba(5, 150, 105, 0.1)' };
            default:
                return { label: `🏢 ${dom || 'General'}`, color: '#6B7280', bg: 'rgba(107, 114, 128, 0.1)' };
        }
    };

    const filteredSessions = sessions.filter(s => {
        const matchesDomain = domainFilter === 'all' || s.domain?.toLowerCase() === currentDomain.toLowerCase();
        const matchesSearch = !searchQuery.trim() || 
            s.title?.toLowerCase().includes(searchQuery.toLowerCase()) || 
            s.last_message?.toLowerCase().includes(searchQuery.toLowerCase()) ||
            s.domain?.toLowerCase().includes(searchQuery.toLowerCase());
        return matchesDomain && matchesSearch;
    });

    return (
        <section className="history-section" id="chat-history-section">
            <div className="history-header">
                <div className="history-header-left">
                    <div className="history-title-row">
                        <div className="history-icon-badge">
                            <MessageSquare size={20} />
                        </div>
                        <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                                <h3>Conversation History</h3>
                                <span className="history-count-badge">
                                    {sessions.length} {sessions.length === 1 ? 'saved chat' : 'saved chats'}
                                </span>
                            </div>
                            <p className="history-subtitle">
                                Reopen past conversations to review answers or continue asking questions from where you left off.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="history-header-actions">
                    {sessions.length > 0 && (
                        confirmClear ? (
                            <div className="confirm-clear-group">
                                <span className="confirm-label">
                                    <AlertTriangle size={14} color="#DC2626" /> Delete all chats?
                                </span>
                                <button 
                                    className="btn-danger-confirm" 
                                    onClick={() => {
                                        onClearAllSessions();
                                        setConfirmClear(false);
                                    }}
                                >
                                    Yes, Clear All
                                </button>
                                <button 
                                    className="btn-cancel" 
                                    onClick={() => setConfirmClear(false)}
                                >
                                    Cancel
                                </button>
                            </div>
                        ) : (
                            <button 
                                className="history-clear-btn" 
                                onClick={() => setConfirmClear(true)}
                                title="Clear all conversation history"
                            >
                                <Trash2 size={14} /> Clear History
                            </button>
                        )
                    )}
                </div>
            </div>

            <div className="history-toolbar">
                <div className="history-search-wrapper">
                    <Search size={15} className="history-search-icon" />
                    <input 
                        type="text"
                        className="history-search-input"
                        placeholder="Search conversations by keyword or question..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                    {searchQuery && (
                        <button className="clear-search-btn" onClick={() => setSearchQuery('')}>×</button>
                    )}
                </div>

                <div className="history-filter-group">
                    <Filter size={14} className="history-filter-icon" />
                    <select 
                        className="history-domain-select"
                        value={domainFilter}
                        onChange={(e) => setDomainFilter(e.target.value as 'all' | 'current')}
                    >
                        <option value="all">All Domains ({sessions.length})</option>
                        <option value="current">Current Domain Only ({sessions.filter(s => s.domain?.toLowerCase() === currentDomain.toLowerCase()).length})</option>
                    </select>
                </div>
            </div>

            {isLoadingSessions ? (
                <div className="history-loading-state">
                    <div className="history-spinner"></div>
                    <p>Loading conversation history...</p>
                </div>
            ) : filteredSessions.length === 0 ? (
                <div className="history-empty-card">
                    <div className="history-empty-icon">
                        <Sparkles size={28} />
                    </div>
                    {sessions.length === 0 ? (
                        <>
                            <h4>No saved conversations yet</h4>
                            <p>Every chat query and response is automatically saved. Type a question above to start building your history!</p>
                        </>
                    ) : (
                        <>
                            <h4>No matching conversations found</h4>
                            <p>No chat sessions matched your search query "{searchQuery}". Try changing your search or filter.</p>
                            <button className="btn-reset-filters" onClick={() => { setSearchQuery(''); setDomainFilter('all'); }}>
                                Reset Filters
                            </button>
                        </>
                    )}
                </div>
            ) : (
                <div className="history-grid">
                    {filteredSessions.map((session) => {
                        const isActive = session.id === activeSessionId;
                        const domainBadge = getDomainBadge(session.domain);

                        return (
                            <div 
                                key={session.id} 
                                className={`history-card ${isActive ? 'active-chat' : ''}`}
                                onClick={() => onResumeSession(session.id)}
                            >
                                <div className="history-card-top">
                                    <div className="history-card-header-left">
                                        <span 
                                            className="history-domain-pill"
                                            style={{ color: domainBadge.color, backgroundColor: domainBadge.bg }}
                                        >
                                            {domainBadge.label}
                                        </span>
                                        {isActive && (
                                            <span className="history-active-badge">
                                                <span className="pulse-dot"></span> Active Now
                                            </span>
                                        )}
                                    </div>

                                    <div className="history-card-meta">
                                        <span className="history-time" title={new Date(session.updated_at).toLocaleString()}>
                                            <Clock size={12} /> {formatTime(session.updated_at)}
                                        </span>
                                        <button 
                                            className="history-delete-item-btn"
                                            title="Delete this conversation"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                if (window.confirm("Are you sure you want to delete this conversation?")) {
                                                    onDeleteSession(session.id);
                                                }
                                            }}
                                        >
                                            <Trash2 size={13} />
                                        </button>
                                    </div>
                                </div>

                                <h4 className="history-card-title" title={session.title}>
                                    {session.title || 'Untitled Conversation'}
                                </h4>

                                {session.last_message && (
                                    <p className="history-card-snippet">
                                        "{session.last_message}"
                                    </p>
                                )}

                                <div className="history-card-footer">
                                    <div className="history-message-count">
                                        <MessageSquare size={13} />
                                        <span>{session.message_count} {session.message_count === 1 ? 'message' : 'messages'}</span>
                                    </div>

                                    <button 
                                        className={`btn-resume-chat ${isActive ? 'btn-resume-active' : ''}`}
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onResumeSession(session.id);
                                        }}
                                    >
                                        <span>{isActive ? 'Current Chat' : 'Resume Conversation'}</span>
                                        <ChevronRight size={14} />
                                    </button>
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
};
