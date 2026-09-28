import { useState } from 'react';
import { useUser, DOMAIN_METAS } from '../../context/UserContext';
import type { DomainType } from '../../context/UserContext';
import { X, Check, CheckCircle2, RotateCcw, Sparkles, Sun, Moon } from 'lucide-react';
import './SettingsModal.css';

export default function SettingsModal() {
    const { user, setDomain, updateProfile, resetProfile, isSettingsOpen, closeSettings, theme, setTheme } = useUser();

    const [accountName, setAccountName] = useState(user.accountName);
    const [organization, setOrganization] = useState(user.organization);
    const [email, setEmail] = useState(user.email);
    const [activeDomain, setActiveDomain] = useState<DomainType>(user.domain);
    const [savedMsg, setSavedMsg] = useState(false);

    if (!isSettingsOpen) return null;

    const handleSave = () => {
        updateProfile({
            accountName: accountName.trim() || 'Admin User',
            organization: organization.trim() || 'My Workspace',
            email: email.trim() || 'admin@llm-konnect.local',
            domain: activeDomain
        });
        setDomain(activeDomain);
        setSavedMsg(true);
        setTimeout(() => {
            setSavedMsg(false);
            closeSettings();
        }, 700);
    };

    const handleResetOnboarding = () => {
        if (window.confirm('Reset onboarding and recreate account profile?')) {
            resetProfile();
            closeSettings();
        }
    };

    return (
        <div className="settings-overlay">
            <div className="settings-card">
                {/* Header */}
                <div className="settings-header">
                    <div>
                        <div className="settings-badge">
                            <Sparkles size={13} /> Workspace Preferences
                        </div>
                        <h2 className="settings-title">Settings &amp; Profile</h2>
                        <p className="settings-subtitle">Manage your account profile, theme, and active domain customization.</p>
                    </div>
                    <button
                        className="settings-close-btn"
                        onClick={closeSettings}
                        title="Close Settings"
                        aria-label="Close"
                    >
                        <X size={17} />
                    </button>
                </div>

                {/* Body */}
                <div className="settings-body">
                    {/* Appearance / Theme Selector */}
                    <div className="settings-section">
                        <div className="settings-section-header">
                            <span className="settings-section-title">Appearance &amp; Theme</span>
                            <span className="settings-section-badge">Google Material Design 3</span>
                        </div>
                        <div className="settings-theme-grid">
                            <button
                                type="button"
                                onClick={() => setTheme('light')}
                                className={`settings-theme-btn ${theme === 'light' ? 'active theme-light' : ''}`}
                            >
                                <Sun size={17} />
                                <span>Light Theme</span>
                            </button>
                            <button
                                type="button"
                                onClick={() => setTheme('dark')}
                                className={`settings-theme-btn ${theme === 'dark' ? 'active theme-dark' : ''}`}
                            >
                                <Moon size={17} />
                                <span>Dark Theme</span>
                            </button>
                        </div>
                    </div>

                    {/* User Profile Form */}
                    <div className="settings-section">
                        <div className="settings-section-header">
                            <span className="settings-section-title">User Profile Details</span>
                        </div>
                        <div className="settings-form-row">
                            <div className="settings-form-group">
                                <label className="settings-label">Account / Admin Name</label>
                                <input
                                    type="text"
                                    className="settings-input"
                                    value={accountName}
                                    onChange={e => setAccountName(e.target.value)}
                                    placeholder="e.g. Fahad"
                                />
                            </div>
                            <div className="settings-form-group">
                                <label className="settings-label">Company / Workspace</label>
                                <input
                                    type="text"
                                    className="settings-input"
                                    value={organization}
                                    onChange={e => setOrganization(e.target.value)}
                                    placeholder="e.g. My Enterprise"
                                />
                            </div>
                        </div>
                        <div className="settings-form-group">
                            <label className="settings-label">Email</label>
                            <input
                                type="email"
                                className="settings-input"
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                                placeholder="e.g. admin@llm-konnect.local"
                            />
                        </div>
                    </div>

                    {/* Active Domain Niche Selector */}
                    <div className="settings-section">
                        <div className="settings-section-header">
                            <span className="settings-section-title">Active Business Domain</span>
                            <span className="settings-section-badge">Changes app interface &amp; schemas</span>
                        </div>

                        <div className="settings-domain-grid">
                            {(Object.keys(DOMAIN_METAS) as DomainType[]).map(key => {
                                const meta = DOMAIN_METAS[key];
                                const isSelected = activeDomain === key;
                                return (
                                    <div
                                        key={key}
                                        className={`settings-domain-card ${isSelected ? 'selected' : ''}`}
                                        onClick={() => setActiveDomain(key)}
                                    >
                                        <div className="settings-domain-top">
                                            <span className="settings-domain-title">{meta.name}</span>
                                            {isSelected && <CheckCircle2 size={16} color="var(--brand-green)" />}
                                        </div>
                                        <p className="settings-domain-desc">{meta.description}</p>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Danger / Reset Zone */}
                    <div className="settings-reset-row">
                        <div>
                            <div className="settings-reset-title">Re-run First-Time Setup</div>
                            <div className="settings-reset-desc">Reset your saved preferences and launch onboarding wizard again.</div>
                        </div>
                        <button
                            type="button"
                            onClick={handleResetOnboarding}
                            className="settings-reset-btn"
                        >
                            <RotateCcw size={13} /> Reset Setup
                        </button>
                    </div>
                </div>

                {/* Footer */}
                <div className="settings-footer">
                    <button className="settings-btn-cancel" onClick={closeSettings}>
                        Cancel
                    </button>
                    <button className="settings-btn-save" onClick={handleSave}>
                        {savedMsg ? (
                            <>Saved <Check size={16} /></>
                        ) : (
                            <>Save Changes <Check size={16} /></>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
