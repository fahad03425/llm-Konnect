import { NavLink } from 'react-router-dom';
import {
    LayoutDashboard,
    Database,
    MessageSquare,
    FileOutput,
    Settings,
    Shield,
    Lock
} from 'lucide-react';
import { useUser } from '../context/UserContext';

const Sidebar = () => {
    const { openSettings, activeDomainMeta } = useUser();

    return (
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
                <button
                    className="nav-item"
                    onClick={openSettings}
                    style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%', cursor: 'pointer' }}
                >
                    <Settings size={18} />
                    <span>Settings</span>
                </button>
                <button
                    className="nav-item"
                    onClick={openSettings}
                    style={{ border: 'none', background: 'none', textAlign: 'left', width: '100%', cursor: 'pointer', marginBottom: '1rem' }}
                >
                    <Shield size={18} />
                    <span>Domain: {activeDomainMeta.name}</span>
                </button>
                <button className="btn-lock" onClick={openSettings}>
                    <Lock size={16} />
                    Account & Lock
                </button>
            </div>
        </aside>
    );
};

export default Sidebar;
