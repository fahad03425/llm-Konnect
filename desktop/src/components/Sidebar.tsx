import { NavLink } from 'react-router-dom';
import {
    LayoutDashboard,
    Database,
    Files,
    MessageSquare,
    FileOutput,
    CalendarDays,
    Settings,
    Shield,
    Lock,
    Sparkles,
    RefreshCw
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useReport } from '../context/ReportContext';

const Sidebar = () => {
    const { openSettings, activeDomainMeta } = useUser();
    const { weeklyIsGenerating, exportIsGenerating } = useReport();

    return (
        <aside className="sidebar">
            <div className="brand">
                <div className="brand-header">
                    <div className="brand-logo-mark">
                        <Sparkles size={16} />
                    </div>
                    <div className="brand-title">LLM-Konnect</div>
                </div>
                <div className="status-badge">
                    <div className="status-dot" />
                    <span>Ollama: Active</span>
                </div>
            </div>

            <nav className="nav-menu">
                <NavLink to="/" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <LayoutDashboard size={19} />
                    <span>Dashboard</span>
                </NavLink>
                <NavLink to="/connect" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <Database size={19} />
                    <span>Connect Source</span>
                </NavLink>
                <NavLink to="/files" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <Files size={19} />
                    <span>Uploaded Files</span>
                </NavLink>
                <NavLink to="/chat" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <MessageSquare size={19} />
                    <span>RAG Chatbot</span>
                </NavLink>
                <NavLink to="/reports" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <FileOutput size={19} />
                    <span style={{ flex: 1 }}>Report Export</span>
                    {exportIsGenerating && (
                        <RefreshCw size={14} className="animate-spin" style={{ color: 'var(--brand-teal)' }} />
                    )}
                </NavLink>
                <NavLink to="/weekly-report" className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}>
                    <CalendarDays size={19} />
                    <span style={{ flex: 1 }}>Weekly Report</span>
                    {weeklyIsGenerating && (
                        <RefreshCw size={14} className="animate-spin" style={{ color: 'var(--brand-teal)' }} />
                    )}
                </NavLink>
            </nav>

            <div className="sidebar-bottom">
                <button
                    className="nav-item"
                    onClick={openSettings}
                >
                    <Settings size={18} />
                    <span>Settings</span>
                </button>
                <button
                    className="nav-item"
                    onClick={openSettings}
                    style={{ marginBottom: '0.25rem' }}
                >
                    <Shield size={18} />
                    <span>Domain: {activeDomainMeta.name}</span>
                </button>
                <button className="btn-lock" onClick={openSettings}>
                    <Lock size={15} />
                    Account &amp; Lock
                </button>
            </div>
        </aside>
    );
};

export default Sidebar;
