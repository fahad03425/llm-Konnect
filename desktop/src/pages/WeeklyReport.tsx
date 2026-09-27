import { useState, useEffect } from 'react';
import {
    CalendarDays,
    Download,
    ShieldCheck,
    RefreshCw,
    Sparkles,
    FileCode,
    Layers,
    AlertCircle,
    TrendingUp,
    AlertTriangle,
    CreditCard,
    PackageX,
    Percent,
    BellRing,
    ExternalLink
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useFilePath } from '../context/FileContext';
import './ReportExport.css';

interface FileOption {
    filename: string;
    file_path: string;
    extension: string;
    file_size_formatted: string;
    dir_type?: string;
    is_ingested?: boolean;
}

interface VerifiedClaim {
    matched_text: string;
    extracted_value: number;
    matched_kpi_key: string | null;
    status: 'verified' | 'mismatch' | 'unmatched';
    expected_value: number | null;
    reason: string | null;
}

interface VerificationReport {
    all_verified: boolean;
    verified_count: number;
    unmatched_count: number;
    mismatch_count: number;
    claims: VerifiedClaim[];
}

interface GeneratedReportPayload {
    kpi_snapshot: Record<string, any>;
    narrative: string;
    verification: VerificationReport;
    verification_passed: boolean;
    regenerated: boolean;
    html_path: string | null;
    pdf_path: string | null;
    charts: Record<string, string>;
    warnings: string[];
}

interface ReportApiResponse {
    report: GeneratedReportPayload;
    download_url_html?: string;
    download_url_pdf?: string;
    download_url?: string;
    domain: string;
    source: string;
}

const API_BASE = '';

const WEEKLY_SECTIONS = [
    { id: 'cash_card_mix', label: '1. Cash vs. Card Mix', icon: CreditCard, desc: 'Settlement breakdown across checkout registers' },
    { id: 'expiry_loss_exposure', label: '2. Expiry Loss Exposure', icon: AlertTriangle, desc: 'Near-term capital risk in 30/60/90 day buckets' },
    { id: 'supplier_credit', label: '3. Supplier Payables', icon: Layers, desc: 'Distributor balances & earliest due dates' },
    { id: 'dead_stock', label: '4. Dead Stock Exposure', icon: PackageX, desc: 'Dormant inventory capital (>90 days without sale)' },
    { id: 'category_margin', label: '5. Margin by Category', icon: Percent, desc: 'Gross profit retention ranked by therapeutic group' },
    { id: 'reorder_alerts', label: '6. Priority Reorders', icon: BellRing, desc: 'Days of supply remaining & stockout risk' },
    { id: 'shrinkage_flags', label: '7. Inventory Shrinkage', icon: AlertCircle, desc: 'Discrepancies between physical stock and sales' },
    { id: 'seasonal_trend', label: '8. Seasonal Patterns', icon: TrendingUp, desc: 'Week-over-week trajectory & forward trend' },
];

export default function WeeklyReport() {
    const { user, activeDomainMeta } = useUser();
    const { activePath } = useFilePath();

    // Configuration Form State
    const [availableFiles, setAvailableFiles] = useState<FileOption[]>([]);
    const [selectedFile, setSelectedFile] = useState<string>('');
    const [businessName, setBusinessName] = useState<string>('Fazal Din & Sons Pharmacy');
    const [formatPdf, setFormatPdf] = useState<boolean>(true);
    const [formatHtml, setFormatHtml] = useState<boolean>(true);
    const [allowRetry, setAllowRetry] = useState<boolean>(true);

    // Generation State
    const [isGenerating, setIsGenerating] = useState<boolean>(false);
    const [generationStep, setGenerationStep] = useState<string>('');
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<ReportApiResponse | null>(null);
    const [previewMode, setPreviewMode] = useState<'html' | 'pdf' | 'audit'>('pdf');

    useEffect(() => {
        document.title = 'Weekly Executive Report — LLM-KONNECT';
        fetchAvailableFiles();
    }, [activePath]);

    const fetchAvailableFiles = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/files`);
            if (res.ok) {
                const data = await res.json();
                const rawFiles: any[] = data.files || [];
                const validExts = ['.csv', '.xlsx', '.xls', '.db', '.json', '.sqlite'];
                const filesList: FileOption[] = rawFiles
                    .filter(f => {
                        const ext = (f.extension || '').toLowerCase();
                        const fname = (f.filename || '').toLowerCase();
                        return validExts.some(ve => ext.endsWith(ve) || fname.endsWith(ve));
                    })
                    .map(f => ({
                        filename: f.filename,
                        file_path: f.file_path,
                        extension: f.extension,
                        file_size_formatted: f.file_size_formatted,
                        dir_type: f.dir_type || 'upload',
                        is_ingested: f.is_ingested || false
                    }));
                setAvailableFiles(filesList);

                if (activePath && filesList.some(f => f.file_path === activePath || f.filename === activePath)) {
                    const matched = filesList.find(f => f.file_path === activePath || f.filename === activePath);
                    if (matched) setSelectedFile(matched.file_path);
                } else if (filesList.length > 0 && !selectedFile) {
                    setSelectedFile(filesList[0].file_path);
                }
            }
        } catch (e) {
            console.warn('Could not fetch file list from backend:', e);
        }
    };

    const handleGenerateWeekly = async () => {
        if (!selectedFile) {
            setError('Please select a data source to generate the weekly report from.');
            return;
        }

        setError(null);
        setIsGenerating(true);
        setGenerationStep('1/5 Computing week-over-week KPIs & payment mix...');

        const formats: string[] = [];
        if (formatHtml) formats.push('html');
        if (formatPdf) formats.push('pdf');
        if (formats.length === 0) formats.push('pdf');

        try {
            const t1 = setTimeout(() => setGenerationStep('2/5 Analyzing distributor payables, dead stock & category margins...'), 800);
            const t2 = setTimeout(() => setGenerationStep('3/5 Scanning for inventory shrinkage & movement anomalies...'), 1800);
            const t3 = setTimeout(() => setGenerationStep('4/5 Generating verified executive lead prose with local AI...'), 3200);
            const t4 = setTimeout(() => setGenerationStep('5/5 Building 9-section publication PDF and interactive HTML...'), 4600);

            const payload = {
                file_path: selectedFile,
                domain: user.domain || 'pharmacy',
                business_name: businessName || 'Pharmacy Weekly Executive Report',
                report_type: 'weekly_pharmacy',
                max_regeneration_attempts: allowRetry ? 1 : 0,
                formats: formats
            };

            const response = await fetch(`${API_BASE}/api/report/generate`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            clearTimeout(t1);
            clearTimeout(t2);
            clearTimeout(t3);
            clearTimeout(t4);

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || `Server error (${response.status})`);
            }

            const data: ReportApiResponse = await response.json();
            setResult(data);
            if (data.download_url_pdf) {
                setPreviewMode('pdf');
            } else if (data.download_url_html) {
                setPreviewMode('html');
            }
        } catch (err: any) {
            setError(err.message || 'Failed to generate weekly report. Please check backend connection.');
        } finally {
            setIsGenerating(false);
            setGenerationStep('');
        }
    };

    return (
        <div className="report-export-container">
            {/* 1. Header */}
            <div className="re-header">
                <div className="re-header-main">
                    <div className="re-icon-badge" style={{ background: 'linear-gradient(135deg, rgba(2, 132, 199, 0.15) 0%, rgba(2, 132, 199, 0.05) 100%)', borderColor: 'rgba(2, 132, 199, 0.25)', color: '#0284c7' }}>
                        <CalendarDays size={28} />
                    </div>
                    <div>
                        <div className="re-title-row">
                            <h1 className="re-title">Weekly Pharmacy Executive Report</h1>
                            <span className="re-badge-live" style={{ background: '#e0f2fe', color: '#0369a1', borderColor: '#bae6fd' }}>
                                <ShieldCheck size={13} /> 7-Day Performance Audit
                            </span>
                            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                {activeDomainMeta.icon} {activeDomainMeta.name}
                            </span>
                        </div>
                        <p className="re-subtitle">
                            Automated 7-day operational intelligence: cash/card mix, expiry risk, supplier debt, dead stock capital, margins, reorder alerts, and inventory shrinkage.
                        </p>
                    </div>
                </div>
            </div>

            {/* 2. Section Capabilities Overview Grid */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '0.75rem', marginBottom: '1.5rem' }}>
                {WEEKLY_SECTIONS.map((sec) => {
                    const IconComp = sec.icon;
                    return (
                        <div key={sec.id} style={{ display: 'flex', alignItems: 'center', gap: '0.65rem', padding: '0.65rem 0.85rem', background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: '8px' }}>
                            <div style={{ width: 30, height: 30, borderRadius: '6px', background: '#f8fafc', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#0284c7', flexShrink: 0 }}>
                                <IconComp size={16} />
                            </div>
                            <div style={{ minWidth: 0 }}>
                                <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#0f172a', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {sec.label}
                                </div>
                                <div style={{ fontSize: '0.72rem', color: '#64748b', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                    {sec.desc}
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* 3. Main Grid */}
            <div className="re-main-grid">
                {/* Left Column: Configuration Form */}
                <div className="re-card" style={{ height: 'fit-content' }}>
                    <div className="re-card-header">
                        <span className="re-card-title">
                            <Layers size={18} color="#0284c7" /> Weekly Report Parameters
                        </span>
                    </div>

                    {error && (
                        <div style={{ padding: '0.75rem', background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', color: '#b91c1c', fontSize: '0.84rem', marginBottom: '1.2rem', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                            <AlertCircle size={16} style={{ flexShrink: 0 }} />
                            <span>{error}</span>
                        </div>
                    )}

                    <div className="re-form-group">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                            <label className="re-label" style={{ margin: 0 }}>Pharmacy POS / Ledger Source</label>
                            <button
                                type="button"
                                onClick={fetchAvailableFiles}
                                title="Refresh files"
                                style={{ background: 'none', border: 'none', color: '#0284c7', cursor: 'pointer', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.25rem', fontWeight: 600 }}
                            >
                                <RefreshCw size={12} /> Refresh
                            </button>
                        </div>

                        {availableFiles.length > 0 ? (
                            <select
                                className="re-select"
                                value={selectedFile}
                                onChange={(e) => setSelectedFile(e.target.value)}
                                disabled={isGenerating}
                            >
                                {availableFiles.map((file, idx) => (
                                    <option key={`file-${idx}`} value={file.file_path}>
                                        {file.filename} ({file.file_size_formatted})
                                    </option>
                                ))}
                            </select>
                        ) : (
                            <div style={{ padding: '0.75rem', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '0.82rem', color: '#64748b' }}>
                                Loading data sources...
                            </div>
                        )}

                        <div style={{ marginTop: '0.5rem', padding: '0.45rem 0.75rem', background: '#f0f9ff', border: '1px solid #bae6fd', borderRadius: '6px', fontSize: '0.78rem', color: '#0369a1', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                            <CalendarDays size={14} style={{ flexShrink: 0 }} />
                            <span>Period: Computes latest 7-day interval vs. previous 7-day baseline automatically.</span>
                        </div>
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Pharmacy Name</label>
                        <input
                            type="text"
                            className="re-input"
                            placeholder="e.g. Fazal Din & Sons Pharmacy"
                            value={businessName}
                            onChange={(e) => setBusinessName(e.target.value)}
                            disabled={isGenerating}
                        />
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Formats to Generate</label>
                        <div className="re-checkbox-group">
                            <label className="re-checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formatPdf}
                                    onChange={(e) => setFormatPdf(e.target.checked)}
                                    disabled={isGenerating}
                                />
                                <span>PDF Document (Print Ready)</span>
                            </label>
                            <label className="re-checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formatHtml}
                                    onChange={(e) => setFormatHtml(e.target.checked)}
                                    disabled={isGenerating}
                                />
                                <span>Interactive HTML Document</span>
                            </label>
                        </div>
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Grounding & Verifier</label>
                        <label className="re-checkbox-label">
                            <input
                                type="checkbox"
                                checked={allowRetry}
                                onChange={(e) => setAllowRetry(e.target.checked)}
                                disabled={isGenerating}
                            />
                            <span>Strict zero-hallucination verification & auto-retry</span>
                        </label>
                    </div>

                    <button
                        className="re-btn-primary"
                        onClick={handleGenerateWeekly}
                        disabled={isGenerating || (!formatPdf && !formatHtml)}
                        style={{ background: 'linear-gradient(135deg, #0284c7 0%, #0369a1 100%)', boxShadow: '0 4px 12px rgba(2, 132, 199, 0.25)' }}
                    >
                        {isGenerating ? (
                            <>
                                <RefreshCw size={18} className="animate-spin" /> Generating Weekly Report...
                            </>
                        ) : (
                            <>
                                <Sparkles size={18} /> Generate Weekly Report
                            </>
                        )}
                    </button>

                    {isGenerating && (
                        <div className="re-loading-box">
                            <div className="re-spinner" style={{ borderTopColor: '#0284c7' }} />
                            <div className="re-loading-step">{generationStep}</div>
                            <div className="re-loading-sub">Running offline on local CPU and Ollama LLM</div>
                        </div>
                    )}
                </div>

                {/* Right Column: Live Document Viewer & Download Links */}
                <div className="re-results-panel">
                    {result ? (
                        <>
                            {/* Verification Verdict Banner */}
                            <div className={`re-verdict-card ${result.report.verification_passed ? 'verified' : 'unverified'}`}>
                                <div className="re-verdict-icon">
                                    {result.report.verification_passed ? '🛡️' : '⚠️'}
                                </div>
                                <div className="re-verdict-content" style={{ flex: 1 }}>
                                    <h3>
                                        {result.report.verification_passed
                                            ? 'Weekly Performance Report Ready'
                                            : 'Verification Notice — Check Metrics'}
                                    </h3>
                                    <p>
                                        {result.report.verification_passed
                                            ? `All executive claims (${result.report.verification.verified_count} verified numbers) cross-checked against the deterministic sales ledger with 0 hallucinations.`
                                            : `Notice: ${result.report.verification.mismatch_count} claim mismatch detected.`}
                                    </p>
                                    <div className="re-verdict-stats">
                                        <span className="re-verdict-pill">
                                            ✓ {result.report.verification.verified_count} Verified Claims
                                        </span>
                                        {result.report.verification.mismatch_count > 0 && (
                                            <span className="re-verdict-pill" style={{ color: '#b91c1c' }}>
                                                ✕ {result.report.verification.mismatch_count} Mismatches
                                            </span>
                                        )}
                                        {result.report.regenerated && (
                                            <span className="re-verdict-pill" style={{ color: '#0369a1' }}>
                                                ⟳ Self-Corrected on Retry
                                            </span>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {/* Document Viewer Container */}
                            <div className="re-doc-viewer-card">
                                {/* Viewer Top Toolbar */}
                                <div className="re-doc-toolbar">
                                    <div className="re-doc-tabs">
                                        <button
                                            className={`re-doc-tab ${previewMode === 'pdf' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('pdf')}
                                            disabled={!result.download_url_pdf}
                                            style={{ opacity: result.download_url_pdf ? 1 : 0.5 }}
                                        >
                                            📑 PDF Document Preview
                                        </button>
                                        <button
                                            className={`re-doc-tab ${previewMode === 'html' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('html')}
                                            disabled={!result.download_url_html}
                                            style={{ opacity: result.download_url_html ? 1 : 0.5 }}
                                        >
                                            📄 Interactive Web Report
                                        </button>
                                        <button
                                            className={`re-doc-tab ${previewMode === 'audit' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('audit')}
                                        >
                                            🔍 Verification Audit Trail
                                        </button>
                                    </div>

                                    {/* Action & Download Buttons */}
                                    <div className="re-doc-actions">
                                        {result.download_url_pdf && (
                                            <a
                                                href={`${API_BASE}${result.download_url_pdf}`}
                                                download
                                                target="_blank"
                                                rel="noreferrer"
                                                className="re-btn-download-pdf"
                                                title="Download Publication-Ready PDF"
                                            >
                                                <Download size={15} /> Download PDF
                                            </a>
                                        )}
                                        {result.download_url_html && (
                                            <a
                                                href={`${API_BASE}${result.download_url_html}`}
                                                download
                                                target="_blank"
                                                rel="noreferrer"
                                                className="re-btn-download-html"
                                                title="Download Self-Contained HTML"
                                            >
                                                <FileCode size={15} /> Download HTML
                                            </a>
                                        )}
                                        {(result.download_url_html || result.download_url_pdf) && (
                                            <a
                                                href={`${API_BASE}${result.download_url_html || result.download_url_pdf}`}
                                                target="_blank"
                                                rel="noreferrer"
                                                className="re-btn-secondary"
                                                title="Open in new window"
                                            >
                                                <ExternalLink size={14} /> Pop Out
                                            </a>
                                        )}
                                    </div>
                                </div>

                                {/* Active Preview Panel */}
                                <div className="re-doc-frame-wrapper">
                                    {previewMode === 'pdf' && result.download_url_pdf && (
                                        <iframe
                                            src={`${API_BASE}${result.download_url_pdf}#toolbar=1&navpanes=0&zoom=page-fit`}
                                            className="re-doc-iframe"
                                            title="PDF Preview"
                                        />
                                    )}

                                    {previewMode === 'html' && result.download_url_html && (
                                        <iframe
                                            src={`${API_BASE}${result.download_url_html}`}
                                            className="re-doc-iframe"
                                            title="Interactive HTML Report"
                                        />
                                    )}

                                    {previewMode === 'audit' && (
                                        <div style={{ padding: '1.5rem', overflowY: 'auto', height: '100%' }}>
                                            <h4 style={{ margin: '0 0 1rem 0', fontSize: '1rem', color: '#0f172a' }}>
                                                Ground-Truth Verification Claims Ledger
                                            </h4>
                                            {result.report.verification.claims.length > 0 ? (
                                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.84rem' }}>
                                                    <thead>
                                                        <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0', textAlign: 'left' }}>
                                                            <th style={{ padding: '0.6rem 0.8rem' }}>Claim Prose</th>
                                                            <th style={{ padding: '0.6rem 0.8rem' }}>Claim Value</th>
                                                            <th style={{ padding: '0.6rem 0.8rem' }}>Expected Value</th>
                                                            <th style={{ padding: '0.6rem 0.8rem' }}>Matched KPI</th>
                                                            <th style={{ padding: '0.6rem 0.8rem' }}>Status</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {result.report.verification.claims.map((claim, idx) => (
                                                            <tr key={idx} style={{ borderBottom: '1px solid #f1f5f9' }}>
                                                                <td style={{ padding: '0.6rem 0.8rem', fontStyle: 'italic', maxWidth: 260 }}>"{claim.matched_text}"</td>
                                                                <td style={{ padding: '0.6rem 0.8rem', fontWeight: 600 }}>{claim.extracted_value?.toLocaleString()}</td>
                                                                <td style={{ padding: '0.6rem 0.8rem', color: '#64748b' }}>{claim.expected_value?.toLocaleString() ?? '—'}</td>
                                                                <td style={{ padding: '0.6rem 0.8rem', fontFamily: 'monospace', color: '#0284c7' }}>{claim.matched_kpi_key || '—'}</td>
                                                                <td style={{ padding: '0.6rem 0.8rem' }}>
                                                                    <span style={{
                                                                        padding: '0.2rem 0.5rem',
                                                                        borderRadius: '4px',
                                                                        fontSize: '0.75rem',
                                                                        fontWeight: 700,
                                                                        background: claim.status === 'verified' ? '#f0fdf4' : '#fef2f2',
                                                                        color: claim.status === 'verified' ? '#16a34a' : '#dc2626'
                                                                    }}>
                                                                        {claim.status.toUpperCase()}
                                                                    </span>
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            ) : (
                                                <div style={{ color: '#64748b', fontSize: '0.88rem' }}>
                                                    All numbers are grounded directly in the deterministic database ledger.
                                                </div>
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </>
                    ) : (
                        <div className="re-empty-placeholder">
                            <div className="re-empty-icon" style={{ background: '#f0f9ff', color: '#0284c7' }}>
                                <CalendarDays size={36} />
                            </div>
                            <h3>Ready to Generate Weekly Executive Report</h3>
                            <p>
                                Select your pharmacy sales or inventory ledger on the left, then click <b>Generate Weekly Report</b> to compute week-over-week performance and build the publication PDF.
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
