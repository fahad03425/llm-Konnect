import { useState } from 'react';
import { useUser, DOMAIN_METAS } from '../../context/UserContext';
import type { DomainType } from '../../context/UserContext';
import { X, Check, CheckCircle2, RotateCcw, Sparkles } from 'lucide-react';

export default function SettingsModal() {
    const { user, setDomain, updateProfile, resetProfile, isSettingsOpen, closeSettings } = useUser();

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
        }, 800);
    };

    const handleResetOnboarding = () => {
        if (window.confirm('Reset onboarding and recreate account profile?')) {
            resetProfile();
            closeSettings();
        }
    };

    return (
        <div className="onboarding-overlay" style={{ animation: 'fadeIn 0.2s ease-out' }}>
            <div className="onboarding-card" style={{ maxWidth: '680px' }}>
                {/* Header */}
                <div className="onboarding-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div>
                        <div className="onboarding-badge">
                            <Sparkles size={14} /> Workspace Preferences
                        </div>
                        <h2 className="onboarding-title" style={{ fontSize: '1.4rem' }}>Settings & Profile</h2>
                        <p className="onboarding-subtitle">Manage your account profile and active domain customization.</p>
                    </div>
                    <button
                        onClick={closeSettings}
                        style={{
                            background: 'rgba(255, 255, 255, 0.08)',
                            border: 'none',
                            color: '#9ca3af',
                            borderRadius: '8px',
                            padding: '0.4rem',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                        }}
                    >
                        <X size={18} />
                    </button>
                </div>

                {/* Body */}
                <div className="onboarding-body" style={{ gap: '1.5rem', maxHeight: '65vh' }}>
                    {/* User Profile Form */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                        <div style={{ fontSize: '0.9rem', fontWeight: 600, color: '#f3f4f6', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '0.4rem' }}>
                            User Profile Details
                        </div>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                            <div className="onboarding-form-group">
                                <label className="onboarding-label">Account / Admin Name</label>
                                <input
                                    type="text"
                                    className="onboarding-input"
                                    value={accountName}
                                    onChange={e => setAccountName(e.target.value)}
                                />
                            </div>
                            <div className="onboarding-form-group">
                                <label className="onboarding-label">Company / Workspace</label>
                                <input
                                    type="text"
                                    className="onboarding-input"
                                    value={organization}
                                    onChange={e => setOrganization(e.target.value)}
                                />
                            </div>
                        </div>
                        <div className="onboarding-form-group">
                            <label className="onboarding-label">Email</label>
                            <input
                                type="email"
                                className="onboarding-input"
                                value={email}
                                onChange={e => setEmail(e.target.value)}
                            />
                        </div>
                    </div>

                    {/* Active Domain Niche Selector */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '0.4rem' }}>
                            <span style={{ fontSize: '0.9rem', fontWeight: 600, color: '#f3f4f6' }}>Active Business Domain</span>
                            <span style={{ fontSize: '0.75rem', color: '#9ca3af' }}>Changes app interface & schemas</span>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.75rem' }}>
                            {(Object.keys(DOMAIN_METAS) as DomainType[]).map(key => {
                                const meta = DOMAIN_METAS[key];
                                const isSelected = activeDomain === key;
                                return (
                                    <div
                                        key={key}
                                        className={`onboarding-niche-card ${isSelected ? 'selected' : ''}`}
                                        onClick={() => setActiveDomain(key)}
                                        style={{ padding: '0.9rem' }}
                                    >
                                        <div className="onboarding-niche-top">
                                            <span style={{ fontSize: '1.3rem' }}>{meta.icon}</span>
                                            {isSelected && <CheckCircle2 size={16} color="#10b981" />}
                                        </div>
                                        <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#ffffff' }}>{meta.name}</div>
                                        <p style={{ fontSize: '0.76rem', color: '#9ca3af', margin: 0, lineHeight: 1.35 }}>{meta.description}</p>
                                    </div>
                                );
                            })}
                        </div>
                    </div>

                    {/* Danger / Reset Zone */}
                    <div style={{ paddingTop: '0.5rem', borderTop: '1px solid rgba(255,255,255,0.08)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                            <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#ef4444' }}>Re-run First-Time Setup</div>
                            <div style={{ fontSize: '0.75rem', color: '#9ca3af' }}>Reset your saved preferences and launch onboarding wizard again.</div>
                        </div>
                        <button
                            type="button"
                            onClick={handleResetOnboarding}
                            style={{
                                background: 'rgba(239, 68, 68, 0.1)',
                                border: '1px solid rgba(239, 68, 68, 0.3)',
                                color: '#ef4444',
                                padding: '0.4rem 0.75rem',
                                borderRadius: '6px',
                                fontSize: '0.8rem',
                                fontWeight: 600,
                                cursor: 'pointer',
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: '0.3rem'
                            }}
                        >
                            <RotateCcw size={13} /> Reset Setup
                        </button>
                    </div>
                </div>

                {/* Footer */}
                <div className="onboarding-footer">
                    <button className="btn-onboarding-back" onClick={closeSettings}>
                        Cancel
                    </button>
                    <button className="btn-onboarding-next" onClick={handleSave}>
                        {savedMsg ? (
                            <>Saved Successfully <Check size={16} /></>
                        ) : (
                            <>Save Changes <Check size={16} /></>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
