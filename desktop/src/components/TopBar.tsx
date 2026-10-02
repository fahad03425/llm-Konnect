import { useState, useRef, useEffect } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import {
    Search,
    Bell,
    HelpCircle,
    SlidersHorizontal,
    Sun,
    Moon,
    CheckCheck,
    Trash2,
    Cpu,
    Database,
    FileCheck2,
    Sparkles,
    RefreshCw
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useReport } from '../context/ReportContext';
import './TopBar.css';

interface NotificationItem {
    id: string;
    title: string;
    message: string;
    timestamp: string;
    type: 'system' | 'data' | 'report';
    unread: boolean;
}

const DEFAULT_NOTIFICATIONS: NotificationItem[] = [
    {
        id: 'notif-1',
        title: 'Local AI Engine Active',
        message: 'Ollama local inference ready with offline zero-hallucination verification.',
        timestamp: '5m ago',
        type: 'system',
        unread: true,
    },
    {
        id: 'notif-2',
        title: 'Deterministic Ledger Synced',
        message: 'pharmacy_dataset.csv loaded into vector memory (89.0 MB).',
        timestamp: '25m ago',
        type: 'data',
        unread: false,
    }
];

const TopBar = () => {
    const location = useLocation();
    const navigate = useNavigate();
    const { user, activeDomainMeta, openSettings, theme, toggleTheme } = useUser();
    const { isAnyReportGenerating } = useReport();

    // Notification dropdown state
    const [isNotifOpen, setIsNotifOpen] = useState(false);
    const [notifications, setNotifications] = useState<NotificationItem[]>(DEFAULT_NOTIFICATIONS);
    const notifRef = useRef<HTMLDivElement>(null);

    const unreadCount = notifications.filter(n => n.unread).length;

    // Close on click outside or Escape
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (notifRef.current && !notifRef.current.contains(e.target as Node)) {
                setIsNotifOpen(false);
            }
        };

        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') {
                setIsNotifOpen(false);
            }
        };

        if (isNotifOpen) {
            document.addEventListener('mousedown', handleClickOutside);
            document.addEventListener('keydown', handleKeyDown);
        }
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
            document.removeEventListener('keydown', handleKeyDown);
        };
    }, [isNotifOpen]);

    const markAllAsRead = () => {
        setNotifications(prev => prev.map(n => ({ ...n, unread: false })));
    };

    const clearAll = () => {
        setNotifications([]);
    };

    const toggleItemRead = (id: string) => {
        setNotifications(prev =>
            prev.map(n => (n.id === id ? { ...n, unread: !n.unread } : n))
        );
    };

    const simulateNewAlert = () => {
        const newAlert: NotificationItem = {
            id: `notif-${Date.now()}`,
            title: 'Audit Verification Complete',
            message: '7-Day weekly report computed with 100% deterministic grounding.',
            timestamp: 'Just now',
            type: 'report',
            unread: true,
        };
        setNotifications(prev => [newAlert, ...prev]);
    };

    const getPageTitle = (path: string) => {
        switch (path) {
            case '/': return 'Dashboard';
            case '/connect': return 'Connect Source';
            case '/files': return 'Uploaded Files & Datasets';
            case '/chat': return 'RAG Chatbot';
            case '/weekly-report': return 'Weekly Report';
            default: return 'LLM-Konnect';
        }
    };

    const userInitials = user.accountName
        ? user.accountName.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
        : 'FA';

    const displayName = user.accountName || 'Fahad';

    const renderTypeIcon = (type: NotificationItem['type']) => {
        switch (type) {
            case 'system':
                return <Cpu size={16} />;
            case 'data':
                return <Database size={16} />;
            case 'report':
                return <FileCheck2 size={16} />;
            default:
                return <Sparkles size={16} />;
        }
    };

    return (
        <header className="topbar">
            <div className="topbar-left">
                <div className="page-title-group">
                    <h1 className="page-title">{getPageTitle(location.pathname)}</h1>
                </div>

                {/* Active Domain Assist Chip (Google Material 3) */}
                <button
                    className="domain-chip"
                    onClick={openSettings}
                    title="Current active domain niche. Click to change in Settings."
                    type="button"
                >
                    <span className="domain-status-dot" />
                    <span className="domain-name">{activeDomainMeta.name}</span>
                    <SlidersHorizontal size={12} className="domain-chip-icon" />
                </button>

                {/* Active Background Generation Indicator */}
                {isAnyReportGenerating && (
                    <button
                        className="domain-chip generating-chip"
                        onClick={() => navigate('/weekly-report')}
                        title="Report generation is actively running in the background. Click to view progress."
                        type="button"
                    >
                        <RefreshCw size={12} className="animate-spin" />
                        <span>Generating Weekly Report...</span>
                    </button>
                )}
            </div>

            {/* Google Centered Search Pill with Shortcut Badge */}
            <div className="topbar-center">
                <div className="search-box">
                    <Search className="search-icon" />
                    <input
                        type="text"
                        className="search-input"
                        placeholder="Search knowledge base, files, or ask KonnectAI..."
                    />
                    <div className="search-kbd-badge">
                        <kbd>Ctrl</kbd> <kbd>K</kbd>
                    </div>
                </div>
            </div>

            <div className="topbar-right">
                <div className="topbar-icons">
                    {/* Theme Toggle */}
                    <button
                        className="icon-btn"
                        onClick={toggleTheme}
                        title={theme === 'dark' ? 'Switch to Light Theme' : 'Switch to Dark Theme'}
                        type="button"
                    >
                        {theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}
                    </button>

                    {/* Google Notification Popover */}
                    <div className="notif-wrapper" ref={notifRef}>
                        <button
                            className={`icon-btn notif-btn ${isNotifOpen ? 'active' : ''}`}
                            onClick={() => setIsNotifOpen(prev => !prev)}
                            title="Notifications"
                            type="button"
                            aria-expanded={isNotifOpen}
                        >
                            <Bell size={19} />
                            {unreadCount > 0 && <span className="notif-badge-dot" />}
                        </button>

                        {isNotifOpen && (
                            <div className="notif-dropdown">
                                {/* Header */}
                                <div className="notif-header">
                                    <div className="notif-title-group">
                                        <h3 className="notif-title">Notifications</h3>
                                        <span className={`notif-count-pill ${unreadCount > 0 ? 'has-unread' : ''}`}>
                                            {unreadCount > 0 ? `${unreadCount} new` : '0 new'}
                                        </span>
                                    </div>
                                    <div className="notif-actions">
                                        {notifications.length > 0 && (
                                            <>
                                                {unreadCount > 0 && (
                                                    <button
                                                        type="button"
                                                        className="notif-action-btn"
                                                        onClick={markAllAsRead}
                                                        title="Mark all as read"
                                                    >
                                                        <CheckCheck size={14} style={{ display: 'inline', marginRight: 3 }} />
                                                        Read
                                                    </button>
                                                )}
                                                <button
                                                    type="button"
                                                    className="notif-action-btn"
                                                    onClick={clearAll}
                                                    title="Clear all notifications"
                                                >
                                                    <Trash2 size={13} style={{ display: 'inline', marginRight: 3 }} />
                                                    Clear
                                                </button>
                                            </>
                                        )}
                                    </div>
                                </div>

                                {/* Body: List or Empty State */}
                                {notifications.length > 0 ? (
                                    <div className="notif-list">
                                        {notifications.map(item => (
                                            <div
                                                key={item.id}
                                                className={`notif-item ${item.unread ? 'unread' : ''}`}
                                                onClick={() => toggleItemRead(item.id)}
                                                role="button"
                                                tabIndex={0}
                                            >
                                                <div className={`notif-icon-box ${item.type}`}>
                                                    {renderTypeIcon(item.type)}
                                                </div>
                                                <div className="notif-content">
                                                    <div className="notif-item-top">
                                                        <span className="notif-item-title">{item.title}</span>
                                                        <span className="notif-item-time">{item.timestamp}</span>
                                                    </div>
                                                    <p className="notif-item-msg">{item.message}</p>
                                                </div>
                                                {item.unread && <span className="notif-unread-indicator" />}
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="notif-empty">
                                        <div className="notif-empty-icon">
                                            <Bell size={24} />
                                        </div>
                                        <h4 className="notif-empty-title">All caught up!</h4>
                                        <p className="notif-empty-desc">
                                            No new notifications right now. System alerts, data syncs, and reports will appear here.
                                        </p>
                                        <button
                                            type="button"
                                            className="notif-empty-btn"
                                            onClick={simulateNewAlert}
                                        >
                                            Simulate Alert
                                        </button>
                                    </div>
                                )}

                                {/* Footer */}
                                <div className="notif-footer">
                                    <span className="notif-footer-status">
                                        <span className="notif-dot-live" /> Local Engine Active
                                    </span>
                                    <button
                                        type="button"
                                        className="notif-footer-link"
                                        onClick={() => {
                                            setIsNotifOpen(false);
                                            openSettings();
                                        }}
                                    >
                                        Preferences
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Help Button */}
                    <button className="icon-btn" title="Help & Documentation" type="button">
                        <HelpCircle size={19} />
                    </button>
                </div>

                <div
                    className="user-profile"
                    onClick={openSettings}
                    title="Google Account & Profile Settings"
                >
                    <div className="user-label">
                        {displayName}
                    </div>
                    <div className="user-avatar" style={{ background: activeDomainMeta.color || 'var(--google-blue)' }}>
                        {userInitials}
                    </div>
                </div>
            </div>
        </header>
    );
};

export default TopBar;
