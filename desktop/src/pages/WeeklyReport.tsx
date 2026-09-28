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
    const { user } = useUser();
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
                    <div className="re-icon-badge">
                        <CalendarDays size={24} />
                    </div>
                    <div>
                        <div className="re-title-row">
                            <h1 className="re-title">Weekly Executive Report</h1>
                            <span className="re-badge-live">
                                <ShieldCheck size={12} /> 7-Day Audit
                            </span>
                        </div>
                        <p className="re-subtitle">
                            Automated operational intelligence across settlements, margins, expiry risks, and inventory.
                        </p>
                    </div>
                </div>
            </div>

            {/* 2. Key Audit Pillars (Google Pill Ribbon) */}
            <div className="weekly-pillars-bar">
                <span className="weekly-pillars-label">Coverage:</span>
                <div className="weekly-pillars-list">
                    {WEEKLY_SECTIONS.map((sec) => {
                        const IconComp = sec.icon;
                        return (
                            <div key={sec.id} className="weekly-pillar-chip" title={sec.desc}>
                                <IconComp size={13} className="pillar-chip-icon" />
                                <span>{sec.label.replace(/^\d+\.\s*/, '')}</span>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* 3. Main Grid */}
            <div className="re-main-grid">
                {/* Left Column: Configuration Form */}
                <div className="re-card" style={{ height: 'fit-content' }}>
                    <div className="re-card-header">
                        <span className="re-card-title">
                            <Layers size={17} className="re-card-title-icon" /> Report Parameters
                        </span>
                    </div>

                    {error && (
                        <div className="re-error-box">
                            <AlertCircle size={16} style={{ flexShrink: 0 }} />
                            <span>{error}</span>
                        </div>
                    )}

                    <div className="re-form-group">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                            <label className="re-label" style={{ margin: 0 }}>POS / Ledger Source</label>
                            <button
                                type="button"
                                onClick={fetchAvailableFiles}
                                title="Refresh files"
                                className="re-refresh-btn"
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
                            <div className="re-loading-sources">
                                Loading data sources...
                            </div>
                        )}

                        <div className="re-period-badge">
                            <CalendarDays size={13} style={{ flexShrink: 0 }} />
                            <span>Automatic 7-day interval vs. previous 7-day baseline</span>
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
                        <label className="re-label">Formats</label>
                        <div className="re-checkbox-group">
                            <label className="re-checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formatPdf}
                                    onChange={(e) => setFormatPdf(e.target.checked)}
                                    disabled={isGenerating}
                                />
                                <span>PDF Document</span>
                            </label>
                            <label className="re-checkbox-label">
                                <input
                                    type="checkbox"
                                    checked={formatHtml}
                                    onChange={(e) => setFormatHtml(e.target.checked)}
                                    disabled={isGenerating}
                                />
                                <span>Interactive HTML</span>
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
                            <span>Strict zero-hallucination verification</span>
                        </label>
                    </div>

                    <button
                        className="re-btn-primary"
                        onClick={handleGenerateWeekly}
                        disabled={isGenerating || (!formatPdf && !formatHtml)}
                    >
                        {isGenerating ? (
                            <>
                                <RefreshCw size={16} className="animate-spin" /> Generating Report...
                            </>
                        ) : (
                            <>
                                <Sparkles size={16} /> Generate Weekly Report
                            </>
                        )}
                    </button>

                    {isGenerating && (
                        <div className="re-loading-box">
                            <div className="re-spinner" />
                            <div className="re-loading-step">{generationStep}</div>
                            <div className="re-loading-sub">Running offline on local Ollama engine</div>
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
                                        <div className="re-audit-container">
                                            <h4 className="re-audit-heading">
                                                Ground-Truth Verification Claims Ledger
                                            </h4>
                                            {result.report.verification.claims.length > 0 ? (
                                                <table className="re-audit-table">
                                                    <thead>
                                                        <tr>
                                                            <th>Claim Prose</th>
                                                            <th>Claim Value</th>
                                                            <th>Expected Value</th>
                                                            <th>Matched KPI</th>
                                                            <th>Status</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {result.report.verification.claims.map((claim, idx) => (
                                                            <tr key={idx}>
                                                                <td style={{ fontStyle: 'italic', maxWidth: 260 }}>"{claim.matched_text}"</td>
                                                                <td style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{claim.extracted_value?.toLocaleString()}</td>
                                                                <td>{claim.expected_value?.toLocaleString() ?? '—'}</td>
                                                                <td style={{ fontFamily: 'monospace', color: 'var(--google-blue)' }}>{claim.matched_kpi_key || '—'}</td>
                                                                <td>
                                                                    <span className={claim.status === 'verified' ? 're-audit-pill-verified' : 're-audit-pill-mismatch'}>
                                                                        {claim.status.toUpperCase()}
                                                                    </span>
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            ) : (
                                                <div style={{ color: 'var(--text-tertiary)', fontSize: '0.88rem' }}>
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
                            <div className="re-empty-icon">
                                <CalendarDays size={32} />
                            </div>
                            <h3>Ready to Generate Weekly Report</h3>
                            <p>
                                Select a ledger on the left and click <b>Generate Weekly Report</b> to compute week-over-week performance and build the publication PDF.
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
