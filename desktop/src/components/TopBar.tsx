import { useLocation } from 'react-router-dom';
import { Search, Bell, HelpCircle, SlidersHorizontal } from 'lucide-react';
import { useUser } from '../context/UserContext';

const TopBar = () => {
    const location = useLocation();
    const { user, activeDomainMeta, openSettings } = useUser();

    const getPageTitle = (path: string) => {
        switch (path) {
            case '/': return `${activeDomainMeta.name} Dashboard`;
            case '/connect': return 'Connect Source';
            case '/chat': return 'RAG Chatbot';
            case '/reports': return 'Report Export';
            default: return 'LLM-KONNECT';
        }
    };

    const userInitials = user.accountName
        ? user.accountName.split(' ').map(w => w[0]).join('').toUpperCase().slice(0, 2)
        : 'AD';

    const displayName = user.accountName || 'ADMIN_01';

    return (
        <header className="topbar">
            <div className="topbar-left" style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                <h1 className="page-title">{getPageTitle(location.pathname)}</h1>

                {/* Active Domain Pill */}
                <button
                    onClick={openSettings}
                    title="Current active domain niche. Click to change in Settings."
                    style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '0.4rem',
                        background: 'rgba(255, 255, 255, 0.05)',
                        border: '1px solid rgba(255, 255, 255, 0.12)',
                        padding: '0.25rem 0.65rem',
                        borderRadius: '20px',
                        fontSize: '0.78rem',
                        fontWeight: 600,
                        color: 'var(--text-primary)',
                        cursor: 'pointer',
                        transition: 'all 0.2s'
                    }}
                >
                    <span>{activeDomainMeta.icon}</span>
                    <span>{activeDomainMeta.name}</span>
                    <SlidersHorizontal size={12} color="var(--text-secondary)" />
                </button>
            </div>
            <div className="topbar-right">
                <div className="search-box">
                    <Search className="search-icon" />
                    <input type="text" className="search-input" placeholder={`Search ${activeDomainMeta.name} data or ask AI...`} />
                </div>

                <div className="topbar-icons">
                    <button className="icon-btn" title="Notifications"><Bell size={20} /></button>
                    <button className="icon-btn" title="Help & Docs"><HelpCircle size={20} /></button>
                </div>

                <div className="user-profile" onClick={openSettings} style={{ cursor: 'pointer' }} title="Click to open Profile & Settings">
                    <div className="user-label monospaced" style={{ maxWidth: '120px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {displayName}
                    </div>
                    <div className="user-avatar" style={{ background: activeDomainMeta.color }}>
                        {userInitials}
                    </div>
                </div>
            </div>
        </header>
    );
};

export default TopBar;
