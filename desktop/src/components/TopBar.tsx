import { useLocation } from 'react-router-dom';
import { Search, Bell, HelpCircle } from 'lucide-react';

const TopBar = () => {
    const location = useLocation();

    const getPageTitle = (path: string) => {
        switch (path) {
            case '/': return 'Dashboard';
            case '/connect': return 'Connect Source';
            case '/chat': return 'RAG Chatbot';
            case '/reports': return 'Report Export';
            default: return 'LLM-KONNECT';
        }
    };

    return (
        <header className="topbar">
            <div className="topbar-left">
                <h1 className="page-title">{getPageTitle(location.pathname)}</h1>
            </div>
            <div className="topbar-right">
                <div className="search-box">
                    <Search className="search-icon" />
                    <input type="text" className="search-input" placeholder="Search data or ask AI..." />
                </div>

                <div className="topbar-icons">
                    <button className="icon-btn"><Bell size={20} /></button>
                    <button className="icon-btn"><HelpCircle size={20} /></button>
                </div>

                <div className="user-profile">
                    <div className="user-label monospaced">ADMIN_01</div>
                    <div className="user-avatar">A</div>
                </div>
            </div>
        </header>
    );
};

export default TopBar;
