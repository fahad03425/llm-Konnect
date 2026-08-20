import {
    FileText,
    BarChart2,
    MessageSquare,
    ShieldCheck,
    Globe,
    Clock,
    ChevronRight,
    ArrowRight
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useEffect } from 'react';

export default function ReportExport() {
    useEffect(() => { document.title = 'Report Export — LLM-KONNECT'; }, []);
    return (
        <div className="report-export-container" style={styles.container}>
            <style>{`
                .report-export-container * {
                    box-sizing: border-box;
                }
                .re-card {
                    background-color: white;
                    border-radius: 12px;
                    padding: 1.5rem;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03);
                    border: 1px solid var(--border-color);
                }
                .re-flow-card {
                    flex: 1;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    text-align: center;
                    gap: 1rem;
                    padding: 1.5rem;
                    background-color: white;
                    border-radius: 12px;
                    box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
                    border: 1px solid var(--border-color);
                    border-top: 4px solid var(--accent-teal);
                }
                .re-btn-filled {
                    display: inline-flex;
                    align-items: center;
                    gap: 0.5rem;
                    background-color: var(--accent-teal);
                    color: white;
                    padding: 0.75rem 1.25rem;
                    border-radius: 8px;
                    font-weight: 600;
                    font-size: 0.875rem;
                    text-decoration: none;
                    transition: opacity 0.2s;
                    border: none;
                }
                .re-btn-filled:hover {
                    opacity: 0.9;
                }
                .re-btn-outlined {
                    display: inline-flex;
                    align-items: center;
                    gap: 0.5rem;
                    background-color: transparent;
                    color: var(--accent-teal);
                    padding: 0.75rem 1.25rem;
                    border-radius: 8px;
                    font-weight: 600;
                    font-size: 0.875rem;
                    text-decoration: none;
                    border: 2px solid var(--accent-teal);
                    transition: background-color 0.2s;
                }
                .re-btn-outlined:hover {
                    background-color: rgba(13, 115, 119, 0.05);
                }
            `}</style>

            {/* HEADER */}
            <div style={styles.header}>
                <div style={styles.iconWrapperLarge}>
                    <FileText size={32} color="var(--accent-teal)" />
                </div>
                <div style={styles.headerContent}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
                        <h1 style={styles.title}>Report Export</h1>
                        <span style={styles.badge}>PLANNED FEATURE</span>
                    </div>
                    <p style={styles.subtitle}>Verified, traceable financial reports — coming in the next release.</p>
                </div>
            </div>

            {/* WHAT IS IT */}
            <div className="re-card" style={styles.introCard}>
                <p style={styles.introText}>
                    <strong>LLM-KONNECT</strong> generates pharmacy analytics reports where every number is verified by code before the AI explains it. Unlike standard AI reports, our verifier cross-checks each figure against the deterministic analytics engine — so you get a report you can trust.
                </p>
            </div>

            {/* HOW IT WILL WORK */}
            <div style={styles.section}>
                <h2 style={styles.sectionTitle}>How it will work</h2>
                <div style={styles.flowContainer}>
                    <div className="re-flow-card">
                        <div style={styles.flowIcon}>
                            <BarChart2 size={24} color="white" />
                        </div>
                        <h3 style={styles.flowTitle}>Step 1</h3>
                        <p style={styles.flowText}>Analytics Engine computes all KPIs with provenance</p>
                    </div>

                    <ChevronRight size={24} color="#9CA3AF" style={{ flexShrink: 0 }} />

                    <div className="re-flow-card">
                        <div style={styles.flowIcon}>
                            <MessageSquare size={24} color="white" />
                        </div>
                        <h3 style={styles.flowTitle}>Step 2</h3>
                        <p style={styles.flowText}>LLM narrates a plain-language summary of the results</p>
                    </div>

                    <ChevronRight size={24} color="#9CA3AF" style={{ flexShrink: 0 }} />

                    <div className="re-flow-card">
                        <div style={styles.flowIcon}>
                            <ShieldCheck size={24} color="white" />
                        </div>
                        <h3 style={styles.flowTitle}>Step 3</h3>
                        <p style={styles.flowText}>Verifier cross-checks every number the LLM wrote against the computed values — mismatches are flagged or regenerated</p>
                    </div>
                </div>

                <div style={styles.noteWrapper}>
                    <p style={styles.noteText}>
                        This is what makes LLM-KONNECT different: the AI never guesses a number. Code computes, the LLM narrates, and a verifier checks.
                    </p>
                </div>
            </div>

            {/* PLANNED OUTPUT FORMATS */}
            <div style={styles.section}>
                <h2 style={styles.sectionTitle}>Planned Output Formats</h2>
                <div style={styles.formatsContainer}>
                    <div className="re-card" style={styles.formatCard}>
                        <div style={styles.formatHeader}>
                            <div style={styles.formatIconWrap}>
                                <FileText size={20} color="var(--accent-teal)" />
                            </div>
                            <span style={styles.formatBadge}>PDF · Planned</span>
                        </div>
                        <h3 style={styles.formatTitle}>PDF Report</h3>
                        <p style={styles.formatText}>
                            A downloadable PDF with KPI tables, charts, and a verified AI narrative. Suitable for sharing with accountants or stakeholders.
                        </p>
                    </div>

                    <div className="re-card" style={styles.formatCard}>
                        <div style={styles.formatHeader}>
                            <div style={styles.formatIconWrap}>
                                <Globe size={20} color="var(--accent-teal)" />
                            </div>
                            <span style={styles.formatBadge}>HTML · Planned</span>
                        </div>
                        <h3 style={styles.formatTitle}>HTML Report</h3>
                        <p style={styles.formatText}>
                            An interactive HTML dashboard saved locally. Viewable in any browser without internet.
                        </p>
                    </div>
                </div>
            </div>

            {/* WHY NOT YET */}
            <div className="re-card" style={styles.statusCard}>
                <div style={styles.statusHeader}>
                    <Clock size={24} color="#D97706" />
                    <h3 style={styles.statusTitle}>Why is this not available yet?</h3>
                </div>
                <p style={styles.statusText}>
                    The analytics engine and RAG chatbot are complete and tested. The report generator requires the verifier component to be built — which cross-checks LLM output against computed values. This is the most technically novel part of the system and is currently in active development.
                </p>
            </div>

            {/* BOTTOM CTA */}
            <div style={styles.ctaSection}>
                <p style={styles.ctaText}>
                    While reports are being built, use the RAG Chatbot for instant plain-language insights, or the Dashboard for KPI overview.
                </p>
                <div style={styles.ctaButtons}>
                    <Link to="/" className="re-btn-filled">
                        Go to Dashboard <ArrowRight size={16} />
                    </Link>
                    <Link to="/chat" className="re-btn-outlined">
                        Open Chatbot <MessageSquare size={16} />
                    </Link>
                </div>
            </div>

        </div>
    );
}

// Inline Styles Object for explicit Premium styling
const styles: Record<string, React.CSSProperties> = {
    container: {
        display: 'flex',
        flexDirection: 'column',
        gap: '2.5rem',
        padding: '2rem 3rem',
        maxWidth: '1200px',
        margin: '0 auto',
        height: '100%',
        overflowY: 'auto',
        backgroundColor: 'var(--primary-bg)'
    },
    header: {
        display: 'flex',
        alignItems: 'center',
        gap: '1.5rem',
        borderBottom: '1px solid var(--border-color)',
        paddingBottom: '2rem',
    },
    iconWrapperLarge: {
        width: '64px',
        height: '64px',
        backgroundColor: 'rgba(13, 115, 119, 0.1)',
        borderRadius: '16px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0
    },
    headerContent: {
        display: 'flex',
        flexDirection: 'column',
        gap: '0.5rem'
    },
    title: {
        fontSize: '2rem',
        fontWeight: 700,
        color: 'var(--text-primary)',
        letterSpacing: '-0.025em',
        margin: 0
    },
    badge: {
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '0.75rem',
        fontWeight: 600,
        color: 'var(--accent-teal)',
        backgroundColor: 'rgba(13, 115, 119, 0.1)',
        padding: '0.25rem 0.75rem',
        borderRadius: '999px',
        letterSpacing: '0.05em'
    },
    subtitle: {
        fontSize: '1.125rem',
        color: '#6B7280',
        margin: 0
    },
    introCard: {
        borderLeft: '4px solid var(--accent-teal)',
        fontSize: '1.125rem',
        lineHeight: 1.6,
        color: 'var(--text-primary)',
    },
    introText: {
        margin: 0
    },
    section: {
        display: 'flex',
        flexDirection: 'column',
        gap: '1.5rem'
    },
    sectionTitle: {
        fontSize: '1.25rem',
        fontWeight: 600,
        color: 'var(--text-primary)',
        margin: 0
    },
    flowContainer: {
        display: 'flex',
        alignItems: 'center',
        gap: '1rem',
    },
    flowIcon: {
        width: '48px',
        height: '48px',
        backgroundColor: 'var(--accent-teal)',
        borderRadius: '12px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        marginBottom: '0.5rem'
    },
    flowTitle: {
        fontSize: '1.125rem',
        fontWeight: 600,
        color: 'var(--text-primary)',
        margin: 0
    },
    flowText: {
        fontSize: '0.9375rem',
        color: '#6B7280',
        lineHeight: 1.5,
        margin: 0
    },
    noteWrapper: {
        marginTop: '0.5rem',
        padding: '1rem 1.5rem',
        backgroundColor: '#F3F4F6',
        borderRadius: '8px',
        borderLeft: '3px solid #9CA3AF'
    },
    noteText: {
        margin: 0,
        fontSize: '0.9375rem',
        color: '#4B5563',
        fontWeight: 500,
        fontStyle: 'italic'
    },
    formatsContainer: {
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: '1.5rem'
    },
    formatCard: {
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem'
    },
    formatHeader: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between'
    },
    formatIconWrap: {
        width: '40px',
        height: '40px',
        backgroundColor: 'rgba(13, 115, 119, 0.1)',
        borderRadius: '8px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
    },
    formatBadge: {
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: '0.75rem',
        fontWeight: 500,
        color: '#6B7280',
        backgroundColor: '#F3F4F6',
        padding: '0.25rem 0.5rem',
        borderRadius: '6px'
    },
    formatTitle: {
        fontSize: '1.125rem',
        fontWeight: 600,
        color: 'var(--text-primary)',
        margin: 0
    },
    formatText: {
        fontSize: '0.9375rem',
        color: '#6B7280',
        lineHeight: 1.5,
        margin: 0
    },
    statusCard: {
        borderLeft: '4px solid #D97706',
        backgroundColor: '#FFFBEB',
        borderColor: '#FDE68A',
        display: 'flex',
        flexDirection: 'column',
        gap: '1rem'
    },
    statusHeader: {
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem'
    },
    statusTitle: {
        fontSize: '1.125rem',
        fontWeight: 600,
        color: '#92400E',
        margin: 0
    },
    statusText: {
        fontSize: '0.9375rem',
        color: '#92400E',
        lineHeight: 1.6,
        margin: 0
    },
    ctaSection: {
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        textAlign: 'center',
        gap: '1.5rem',
        padding: '3rem 2rem',
        backgroundColor: 'white',
        borderRadius: '12px',
        boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.05)',
        border: '1px solid var(--border-color)',
        marginTop: '1rem'
    },
    ctaText: {
        fontSize: '1.125rem',
        color: 'var(--text-primary)',
        fontWeight: 500,
        maxWidth: '600px',
        margin: 0,
        lineHeight: 1.5
    },
    ctaButtons: {
        display: 'flex',
        alignItems: 'center',
        gap: '1rem'
    }
};
