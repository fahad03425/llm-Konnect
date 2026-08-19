import { Outlet, NavLink, useLocation } from 'react-router-dom';
import {
    LayoutDashboard,
    Database,
    MessageSquare,
    FileOutput,
    Settings,
    Shield,
    Lock,
    Search,
    Bell,
    HelpCircle
} from 'lucide-react';

const Shell = () => {
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
        <div className="app-container">
            {/* Sidebar */}
            <aside className="sidebar">
                <div className="brand">
                    <div className="brand-title">LLM-KONNECT</div>
                    <div className="status-badge monospaced">
                        <div className="status-dot" />
                        Ollama Engine: RUNNING
                    </div>
                </div>

                <nav className="nav-menu">
                    <NavLink to="/" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                        <LayoutDashboard size={18} />
                        <span>Dashboard</span>
                    </NavLink>
                    <NavLink to="/connect" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                        <Database size={18} />
                        <span>Connect Source</span>
                    </NavLink>
                    <NavLink to="/chat" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                        <MessageSquare size={18} />
                        <span>RAG Chatbot</span>
                    </NavLink>
                    <NavLink to="/reports" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                        <FileOutput size={18} />
                        <span>Report Export</span>
                    </NavLink>
                </nav>

                <div className="sidebar-bottom">
                    <button className="nav-item" style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%', cursor: 'pointer' }}>
                        <Settings size={18} />
                        <span>Settings</span>
                    </button>
                    <button className="nav-item" style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%', cursor: 'pointer', marginBottom: '1rem' }}>
                        <Shield size={18} />
                        <span>Security</span>
                    </button>
                    <button className="btn-lock">
                        <Lock size={16} />
                        Lock System
                    </button>
                </div>
            </aside>

            {/* Main Content Area */}
            <div className="main-wrapper">
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

                <main className="page-content">
                    <Outlet />
                </main>
            </div>
        </div>
    );
};

export default Shell;
