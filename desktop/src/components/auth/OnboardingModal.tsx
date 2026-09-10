import { useState } from 'react';
import { useUser, DOMAIN_METAS } from '../../context/UserContext';
import type { DomainType } from '../../context/UserContext';
import { CheckCircle2, ArrowRight, ArrowLeft, Sparkles, Building, User } from 'lucide-react';
import './Onboarding.css';

export default function OnboardingModal() {
    const { user, completeOnboarding } = useUser();

    // Only render if setup has not been completed
    if (user.isSetupComplete) {
        return null;
    }

    const [step, setStep] = useState<number>(1);
    const [accountName, setAccountName] = useState(user.accountName || '');
    const [organization, setOrganization] = useState(user.organization || '');
    const [email, setEmail] = useState(user.email || '');
    const [selectedDomain, setSelectedDomain] = useState<DomainType>(user.domain || 'pharmacy');
    const [error, setError] = useState<string | null>(null);

    const handleNext = () => {
        if (step === 1) {
            if (!accountName.trim()) {
                setError('Please enter your name or admin username');
                return;
            }
            setError(null);
            setStep(2);
        } else if (step === 2) {
            setStep(3);
        } else if (step === 3) {
            completeOnboarding({
                accountName: accountName.trim(),
                organization: organization.trim() || 'My Enterprise',
                email: email.trim() || 'admin@llm-konnect.local',
                domain: selectedDomain
            });
        }
    };

    const handleBack = () => {
        setError(null);
        setStep(prev => Math.max(1, prev - 1));
    };

    const domainMeta = DOMAIN_METAS[selectedDomain];

    return (
        <div className="onboarding-overlay">
            <div className="onboarding-card">
                {/* Header */}
                <div className="onboarding-header">
                    <div className="onboarding-badge">
                        <Sparkles size={14} /> Initial Setup & Customization
                    </div>
                    <h2 className="onboarding-title">Welcome to LLM-KONNECT</h2>
                    <p className="onboarding-subtitle">
                        {step === 1 && "Let's set up your administrator profile to get started with local data intelligence."}
                        {step === 2 && "Choose your primary business domain. The entire interface, data ingestion, and AI pipelines will be tailored for you."}
                        {step === 3 && "Review your configuration and launch your customized LLM-KONNECT workspace."}
                    </p>

                    {/* Stepper */}
                    <div className="onboarding-stepper">
                        <div className={`stepper-step ${step === 1 ? 'active' : step > 1 ? 'completed' : ''}`}>
                            <div className="stepper-num">{step > 1 ? '✓' : '1'}</div>
                            <span>Profile</span>
                        </div>
                        <div className="stepper-divider" />
                        <div className={`stepper-step ${step === 2 ? 'active' : step > 2 ? 'completed' : ''}`}>
                            <div className="stepper-num">{step > 2 ? '✓' : '2'}</div>
                            <span>Choose Niche</span>
                        </div>
                        <div className="stepper-divider" />
                        <div className={`stepper-step ${step === 3 ? 'active' : ''}`}>
                            <div className="stepper-num">3</div>
                            <span>Launch</span>
                        </div>
                    </div>
                </div>

                {/* Body Content */}
                <div className="onboarding-body">
                    {step === 1 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                            <div className="onboarding-form-group">
                                <label className="onboarding-label">Your Name / Username *</label>
                                <div style={{ position: 'relative' }}>
                                    <input
                                        type="text"
                                        className="onboarding-input"
                                        style={{ width: '100%', boxSizing: 'border-box' }}
                                        placeholder="e.g. Dr. Alex Mercer / John Doe"
                                        value={accountName}
                                        onChange={e => setAccountName(e.target.value)}
                                        autoFocus
                                    />
                                </div>
                            </div>

                            <div className="onboarding-form-group">
                                <label className="onboarding-label">Company / Organization Name</label>
                                <input
                                    type="text"
                                    className="onboarding-input"
                                    placeholder="e.g. Apex HealthCare / Global Retail Ltd."
                                    value={organization}
                                    onChange={e => setOrganization(e.target.value)}
                                />
                            </div>

                            <div className="onboarding-form-group">
                                <label className="onboarding-label">Email Address (Optional)</label>
                                <input
                                    type="email"
                                    className="onboarding-input"
                                    placeholder="e.g. admin@company.com"
                                    value={email}
                                    onChange={e => setEmail(e.target.value)}
                                />
                            </div>

                            {error && (
                                <div style={{ color: '#ef4444', fontSize: '0.85rem', fontWeight: 500 }}>
                                    {error}
                                </div>
                            )}
                        </div>
                    )}

                    {step === 2 && (
                        <div>
                            <div className="onboarding-niche-grid">
                                {(Object.keys(DOMAIN_METAS) as DomainType[]).map(key => {
                                    const meta = DOMAIN_METAS[key];
                                    const isSelected = selectedDomain === key;
                                    return (
                                        <div
                                            key={key}
                                            className={`onboarding-niche-card ${isSelected ? 'selected' : ''}`}
                                            onClick={() => setSelectedDomain(key)}
                                        >
                                            <div className="onboarding-niche-top">
                                                <span className="onboarding-niche-icon">{meta.icon}</span>
                                                {isSelected && <CheckCircle2 size={18} color="#10b981" />}
                                            </div>
                                            <div className="onboarding-niche-title">{meta.name}</div>
                                            <p className="onboarding-niche-desc">{meta.description}</p>
                                            <span className="onboarding-niche-tag">
                                                {isSelected ? 'Selected Domain' : 'Click to select'}
                                            </span>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {step === 3 && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
                            <div className="onboarding-summary-box">
                                <div className="summary-row">
                                    <span className="summary-label">Administrator</span>
                                    <span className="summary-val"><User size={16} color="#10b981" /> {accountName || 'Admin'}</span>
                                </div>
                                <div className="summary-row">
                                    <span className="summary-label">Organization</span>
                                    <span className="summary-val"><Building size={16} color="#10b981" /> {organization || 'My Enterprise'}</span>
                                </div>
                                <div className="summary-row">
                                    <span className="summary-label">Selected Business Niche</span>
                                    <span className="summary-val" style={{ color: domainMeta.color }}>
                                        {domainMeta.icon} {domainMeta.name}
                                    </span>
                                </div>
                                <div className="summary-row">
                                    <span className="summary-label">Ingestion Schema</span>
                                    <span className="summary-val">Custom canonical validation & RAG active</span>
                                </div>
                            </div>

                            <div style={{ background: 'rgba(16, 185, 129, 0.08)', border: '1px solid rgba(16, 185, 129, 0.2)', borderRadius: '8px', padding: '1rem', fontSize: '0.85rem', color: '#d1d5db', lineHeight: 1.5 }}>
                                ✨ <strong>Tailored Workspace Ready:</strong> Connect Source, Knowledge Base indexing, Dashboard metrics, and AI queries will now operate automatically in <strong>{domainMeta.name}</strong> mode. You can switch domains anytime in Settings.
                            </div>
                        </div>
                    )}
                </div>

                {/* Footer Controls */}
                <div className="onboarding-footer">
                    {step > 1 ? (
                        <button className="btn-onboarding-back" onClick={handleBack}>
                            <ArrowLeft size={16} /> Back
                        </button>
                    ) : (
                        <div />
                    )}

                    <button className="btn-onboarding-next" onClick={handleNext}>
                        {step === 3 ? (
                            <>Launch Workspace <CheckCircle2 size={16} /></>
                        ) : (
                            <>Continue <ArrowRight size={16} /></>
                        )}
                    </button>
                </div>
            </div>
        </div>
    );
}
