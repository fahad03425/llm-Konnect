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
                return { label: 'Pharmacy & Health', color: '#10b981', bg: 'rgba(16, 185, 129, 0.12)' };
            case 'retail':
                return { label: 'Retail & E-Commerce', color: '#10b981', bg: 'rgba(16, 185, 129, 0.12)' };
            case 'finance':
                return { label: 'Financial Services', color: '#10b981', bg: 'rgba(16, 185, 129, 0.12)' };
            case 'fmcg':
                return { label: 'FMCG & CPG', color: '#10b981', bg: 'rgba(16, 185, 129, 0.12)' };
            default:
                return { label: dom || 'General', color: '#9ca3af', bg: 'rgba(255, 255, 255, 0.08)' };
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
                            <MessageSquare size={18} />
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                            <h3>Conversation History</h3>
                            <span className="history-count-badge">
                                {sessions.length} {sessions.length === 1 ? 'chat' : 'chats'}
                            </span>
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
                                <Trash2 size={13} /> Clear History
                            </button>
                        )
                    )}
                </div>
            </div>

            <div className="history-toolbar">
                <div className="history-search-wrapper">
                    <Search size={14} className="history-search-icon" />
                    <input 
                        type="text"
                        className="history-search-input"
                        placeholder="Search conversations..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                    {searchQuery && (
                        <button className="clear-search-btn" onClick={() => setSearchQuery('')}>×</button>
                    )}
                </div>

                <div className="history-filter-group">
                    <Filter size={13} className="history-filter-icon" />
                    <select 
                        className="history-domain-select"
                        value={domainFilter}
                        onChange={(e) => setDomainFilter(e.target.value as 'all' | 'current')}
                    >
                        <option value="all">All Domains ({sessions.length})</option>
                        <option value="current">Current Domain ({sessions.filter(s => s.domain?.toLowerCase() === currentDomain.toLowerCase()).length})</option>
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
                    <Sparkles size={24} className="history-empty-icon" />
                    <h4>{sessions.length === 0 ? 'No saved conversations' : 'No matching conversations'}</h4>
                    <p>{sessions.length === 0 ? 'Conversations will appear here as you chat.' : `No chat sessions matched "${searchQuery}".`}</p>
                    {sessions.length > 0 && (
                        <button className="btn-reset-filters" onClick={() => { setSearchQuery(''); setDomainFilter('all'); }}>
                            Reset Filters
                        </button>
                    )}
                </div>
            ) : (
                <div className="history-list">
                    {filteredSessions.map((session) => {
                        const isActive = session.id === activeSessionId;
                        const domainBadge = getDomainBadge(session.domain);

                        return (
                            <div 
                                key={session.id} 
                                className={`history-row ${isActive ? 'active-chat' : ''}`}
                                onClick={() => onResumeSession(session.id)}
                            >
                                <div className="history-row-left">
                                    <div className="history-row-icon">
                                        <MessageSquare size={14} />
                                    </div>
                                    <div className="history-row-content">
                                        <span className="history-row-title" title={session.title}>
                                            {session.title || 'Untitled Conversation'}
                                        </span>
                                        {session.last_message && (
                                            <span className="history-row-snippet" title={session.last_message}>
                                                — {session.last_message}
                                            </span>
                                        )}
                                    </div>
                                </div>

                                <div className="history-row-right">
                                    {session.domain && session.domain.toLowerCase() !== currentDomain?.toLowerCase() && (
                                        <span 
                                            className="history-domain-pill"
                                            style={{ color: domainBadge.color, backgroundColor: domainBadge.bg }}
                                        >
                                            {domainBadge.label}
                                        </span>
                                    )}
                                    {isActive && (
                                        <span className="history-active-badge">
                                            <span className="pulse-dot"></span> Active
                                        </span>
                                    )}
                                    <span className="history-pill-msgs">
                                        {session.message_count} {session.message_count === 1 ? 'msg' : 'msgs'}
                                    </span>
                                    <span className="history-row-time" title={new Date(session.updated_at).toLocaleString()}>
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
                                    <ChevronRight size={14} className="history-row-arrow" />
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
        </section>
    );
};
