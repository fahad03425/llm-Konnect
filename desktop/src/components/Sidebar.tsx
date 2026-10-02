import { useState, useEffect } from 'react';
import { NavLink } from 'react-router-dom';
import {
    LayoutDashboard,
    Database,
    Files,
    MessageSquare,
    CalendarDays,
    Settings,
    Shield,
    Lock,
    Sparkles,
    RefreshCw
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useReport } from '../context/ReportContext';
import { useChat } from '../context/ChatContext';

const Sidebar = () => {
    const { openSettings, activeDomainMeta } = useUser();
    const { weeklyIsGenerating } = useReport();
    const { isLoading: chatIsLoading } = useChat();
    const [ollamaActive, setOllamaActive] = useState<boolean>(true);

    useEffect(() => {
        const checkOllama = async () => {
            try {
                const res = await fetch('/api/chat/models');
                if (res.ok) {
                    const data = await res.json();
                    setOllamaActive(Array.isArray(data.models) && data.models.length > 0);
                } else {
                    setOllamaActive(false);
                }
            } catch {
                setOllamaActive(false);
            }
        };

        checkOllama();
        const interval = setInterval(checkOllama, 10000);
        return () => clearInterval(interval);
    }, []);

    return (
        <aside className="sidebar">
            <div className="brand">
                <div className="brand-header">
                    <div className="brand-logo-mark">
                        <Sparkles size={16} />
                    </div>
                    <div className="brand-title">LLM-Konnect</div>
                </div>
                <div className="status-badge" style={{ borderColor: ollamaActive ? 'rgba(74, 222, 128, 0.25)' : 'rgba(239, 68, 68, 0.25)' }}>
                    <div className="status-dot" style={{ background: ollamaActive ? '#4ade80' : '#ef4444', boxShadow: ollamaActive ? '0 0 8px rgba(74, 222, 128, 0.6)' : '0 0 8px rgba(239, 68, 68, 0.6)' }} />
                    <span style={{ color: ollamaActive ? '#4ade80' : '#f87171' }}>{ollamaActive ? 'Ollama: Active' : 'Ollama: Offline'}</span>
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
                    <span style={{ flex: 1 }}>RAG Chatbot</span>
                    {chatIsLoading && (
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
