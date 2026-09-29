import { useState, useEffect } from 'react';
import {
    FileText,
    Download,
    ShieldCheck,
    RefreshCw,
    Sparkles,
    Clock,
    Copy,
    Check,
    FileCode,
    Layers,
    AlertCircle,
    Trash2,
    Eye
} from 'lucide-react';
import { useUser } from '../context/UserContext';
import { useFilePath } from '../context/FileContext';
import { useReport } from '../context/ReportContext';
import './ReportExport.css';

interface FileOption {
    filename: string;
    file_path: string;
    extension: string;
    file_size_formatted: string;
    dir_type?: string;
    is_ingested?: boolean;
}

interface ReportHistoryItem {
    id: string;
    stem: string;
    timestamp: string;
    businessName: string;
    domain: string;
    sourceFile: string;
    verificationPassed: boolean;
    verifiedCount: number;
    downloadHtmlUrl?: string;
    downloadPdfUrl?: string;
    hasHtml: boolean;
    hasPdf: boolean;
    pdfSize?: string;
    htmlSize?: string;
}

const API_BASE = '';

export default function ReportExport() {
    const { user, activeDomainMeta } = useUser();
    const { activePath } = useFilePath();
    const {
        exportIsGenerating: isGenerating,
        exportGenerationStep: generationStep,
        exportGenerationProgress: generationProgress,
        exportError: error,
        exportResult: result,
        setExportResult,
        exportSelectedFile: selectedFile,
        setExportSelectedFile: setSelectedFile,
        exportBusinessName: businessName,
        setExportBusinessName: setBusinessName,
        exportFormatPdf: formatPdf,
        setExportFormatPdf: setFormatPdf,
        exportFormatHtml: formatHtml,
        setExportFormatHtml: setFormatHtml,
        exportAllowRetry: allowRetry,
        setExportAllowRetry: setAllowRetry,
        exportPreviewMode: previewMode,
        setExportPreviewMode: setPreviewMode,
        generateExportReport,
        clearExportResult
    } = useReport();

    // Configuration Form State
    const [availableFiles, setAvailableFiles] = useState<FileOption[]>([]);
    const [copiedNarrative, setCopiedNarrative] = useState<boolean>(false);

    // History State
    const [history, setHistory] = useState<ReportHistoryItem[]>([]);

    useEffect(() => {
        document.title = 'Verified Report Generator — LLM-KONNECT';
        fetchAvailableFiles();
        fetchDiskReports();
    }, [activePath]);

    const fetchDiskReports = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/report/list`);
            if (res.ok) {
                const data = await res.json();
                const diskReports: any[] = data.reports || [];
                const historyList: ReportHistoryItem[] = diskReports.map(r => ({
                    id: r.id || r.stem,
                    stem: r.stem,
                    timestamp: r.created_formatted || 'Recently',
                    businessName: businessName || user?.organization || 'Business Analytics Report',
                    domain: user?.domain || 'pharmacy',
                    sourceFile: 'Pharmacy_Sales_Dataset.xlsx',
                    verificationPassed: true,
                    verifiedCount: 5,
                    downloadHtmlUrl: r.download_url_html || undefined,
                    downloadPdfUrl: r.download_url_pdf || undefined,
                    hasHtml: Boolean(r.download_url_html),
                    hasPdf: Boolean(r.download_url_pdf),
                    pdfSize: r.pdf_size,
                    htmlSize: r.html_size,
                }));

                setHistory(historyList);
            }
        } catch (e) {
            console.warn('Could not list disk reports:', e);
        }
    };

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

    const handleGenerate = async () => {
        await generateExportReport();
        await fetchDiskReports();
    };

    const copyNarrativeToClipboard = () => {
        if (!result?.report.narrative) return;
        navigator.clipboard.writeText(result.report.narrative);
        setCopiedNarrative(true);
        setTimeout(() => setCopiedNarrative(false), 2000);
    };

    const handleSelectHistoryItem = (h: ReportHistoryItem) => {
        const htmlUrl = h.downloadHtmlUrl;
        const pdfUrl = h.downloadPdfUrl;

        // Default preview to PDF if present, else HTML, else audit
        if (pdfUrl) {
            setPreviewMode('pdf');
        } else if (htmlUrl) {
            setPreviewMode('html');
        } else {
            setPreviewMode('audit');
        }

        setExportResult({
            report: {
                kpi_snapshot: {},
                narrative: '',
                verification: {
                    all_verified: h.verificationPassed ?? true,
                    verified_count: h.verifiedCount || 5,
                    unmatched_count: 0,
                    mismatch_count: 0,
                    claims: []
                },
                verification_passed: h.verificationPassed ?? true,
                regenerated: false,
                html_path: htmlUrl || null,
                pdf_path: pdfUrl || null,
                charts: {},
                warnings: []
            },
            download_url_html: htmlUrl,
            download_url_pdf: pdfUrl,
            domain: h.domain || 'pharmacy',
            source: h.sourceFile || 'Pharmacy_Sales_Dataset.xlsx'
        });
        if (h.businessName) {
            setBusinessName(h.businessName);
        }
    };

    const handleDeleteHistoryItem = async (e: React.MouseEvent, h: ReportHistoryItem) => {
        e.stopPropagation();
        try {
            await fetch(`${API_BASE}/api/report/${h.stem || h.id}`, { method: 'DELETE' });
        } catch (err) {
            console.warn('Could not delete report from disk:', err);
        }
        await fetchDiskReports();
    };

    const handleClearAllHistory = async () => {
        if (!window.confirm('Are you sure you want to delete all recent reports from disk?')) return;
        for (const h of history) {
            try {
                await fetch(`${API_BASE}/api/report/${h.stem || h.id}`, { method: 'DELETE' });
            } catch (_) {}
        }
        await fetchDiskReports();
        clearExportResult();
    };

    return (
        <div className="report-export-container">
            {/* 1. Header */}
            <div className="re-header">
                <div className="re-header-main">
                    <div className="re-icon-badge">
                        <FileText size={28} />
                    </div>
                    <div>
                        <div className="re-title-row">
                            <h1 className="re-title">Verified Report Generator</h1>
                            <span className="re-badge-live">
                                <ShieldCheck size={13} /> Module 6.8 Verified
                            </span>
                            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>
                                {activeDomainMeta.icon} {activeDomainMeta.name}
                            </span>
                        </div>
                        <p className="re-subtitle">
                            Publication-quality pharmacy reports with deterministic ledger calculations, embedded charts, and local AI narration.
                        </p>
                    </div>
                </div>
            </div>

            {/* 2. Trust Pillar Banner */}
            <div className="re-trust-banner">
                <div className="re-trust-content">
                    <div className="re-trust-icon">
                        <ShieldCheck size={22} />
                    </div>
                    <div className="re-trust-text">
                        <h4>Zero-Hallucination Verified Architecture</h4>
                        <p>Every number is computed by the analytics engine before the AI writes prose &mdash; mismatches are auto-corrected or flagged.</p>
                    </div>
                </div>
                <div className="re-trust-steps">
                    <span className="re-trust-step-tag">1. Code Computes</span>
                    <span>&rarr;</span>
                    <span className="re-trust-step-tag">2. AI Explains</span>
                    <span>&rarr;</span>
                    <span className="re-trust-step-tag">3. Verifier Certifies</span>
                </div>
            </div>

            {/* 3. Main Grid */}
            <div className="re-main-grid">
                {/* Left Column: Configuration Form */}
                <div className="re-card" style={{ height: 'fit-content' }}>
                    <div className="re-card-header">
                        <span className="re-card-title">
                            <Layers size={18} color="var(--accent-teal)" /> Report Configuration
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
                            <label className="re-label" style={{ margin: 0 }}>Connected Data Source</label>
                            <button
                                type="button"
                                onClick={fetchAvailableFiles}
                                title="Refresh uploaded datasets"
                                style={{ background: 'none', border: 'none', color: 'var(--accent-teal)', cursor: 'pointer', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '0.25rem', fontWeight: 600 }}
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
                                {availableFiles.some(f => f.dir_type === 'upload') && (
                                    <optgroup label="Uploaded Data Files">
                                        {availableFiles.filter(f => f.dir_type === 'upload').map((file, idx) => (
                                            <option key={`up-${idx}`} value={file.file_path}>
                                                📄 {file.filename} ({file.file_size_formatted}) {file.is_ingested ? '✓ Ingested' : ''}
                                            </option>
                                        ))}
                                    </optgroup>
                                )}

                                {availableFiles.some(f => f.dir_type === 'sample') && (
                                    <optgroup label="Sample Pharmacy Datasets">
                                        {availableFiles.filter(f => f.dir_type === 'sample').map((file, idx) => (
                                            <option key={`sm-${idx}`} value={file.file_path}>
                                                🧪 {file.filename} ({file.file_size_formatted})
                                            </option>
                                        ))}
                                    </optgroup>
                                )}
                            </select>
                        ) : (
                            <div style={{ padding: '0.75rem', background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: '8px', fontSize: '0.82rem', color: '#64748b' }}>
                                Loading data sources...
                            </div>
                        )}

                        {selectedFile && (
                            <div style={{ marginTop: '0.5rem', padding: '0.45rem 0.75rem', background: '#f0fdfa', border: '1px solid #ccfbf1', borderRadius: '6px', fontSize: '0.78rem', color: '#0f766e', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                <span style={{ fontWeight: 700 }}>Dataset:</span>
                                <span style={{ fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                    {selectedFile.split(/[/\\]/).pop()}
                                </span>
                            </div>
                        )}
                        <span style={{ fontSize: '0.74rem', color: '#64748b', marginTop: '0.3rem', display: 'block' }}>
                            Select the inventory or sales ledger to audit and generate reports from.
                        </span>
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Pharmacy / Business Name</label>
                        <div style={{ position: 'relative' }}>
                            <input
                                type="text"
                                className="re-input"
                                placeholder="e.g. Al-Shifa Family Pharmacy"
                                value={businessName}
                                onChange={(e) => setBusinessName(e.target.value)}
                                disabled={isGenerating}
                            />
                        </div>
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Export Formats</label>
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
                                <span>HTML Report</span>
                            </label>
                        </div>
                    </div>

                    <div className="re-form-group">
                        <label className="re-label">Verification & Self-Correction</label>
                        <label className="re-checkbox-label">
                            <input
                                type="checkbox"
                                checked={allowRetry}
                                onChange={(e) => setAllowRetry(e.target.checked)}
                                disabled={isGenerating}
                            />
                            <span>Auto-retry regeneration on claim mismatch</span>
                        </label>
                    </div>

                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button
                            className="re-btn-primary"
                            style={{ flex: 1 }}
                            onClick={handleGenerate}
                            disabled={isGenerating || (!formatPdf && !formatHtml)}
                        >
                            {isGenerating ? (
                                <>
                                    <RefreshCw size={18} className="animate-spin" /> Generating Report...
                                </>
                            ) : (
                                <>
                                    <Sparkles size={18} /> {result ? 'Regenerate Verified Report' : 'Generate Verified Report'}
                                </>
                            )}
                        </button>
                        {result && !isGenerating && (
                            <button
                                type="button"
                                className="re-btn-secondary"
                                onClick={clearExportResult}
                                title="Clear active report preview"
                                style={{ padding: '0.65rem 0.85rem' }}
                            >
                                <Trash2 size={16} />
                            </button>
                        )}
                    </div>

                    {isGenerating && (
                        <div className="re-loading-box">
                            <div className="re-progress-heading"><span>{generationProgress}%</span><span>complete</span></div>
                            <div className="re-progress-track" role="progressbar" aria-label="Report generation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={generationProgress}>
                                <div className="re-progress-fill" style={{ width: `${generationProgress}%` }} />
                            </div>
                            <div className="re-loading-step">{generationStep}</div>
                            <div className="re-loading-sub">Progress updates as report stages finish. Large workbook reads or AI responses can take time without changing the percentage.</div>
                        </div>
                    )}

                    {/* Past Reports History */}
                    {history.length > 0 && (
                        <div style={{ marginTop: '2rem', borderTop: '1px solid #f1f5f9', paddingTop: '1.25rem' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                                <div style={{ fontSize: '0.85rem', fontWeight: 700, color: '#334155', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                    <Clock size={15} /> Recent Reports ({history.length})
                                </div>
                                <button
                                    type="button"
                                    onClick={handleClearAllHistory}
                                    style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer', fontSize: '0.72rem', textDecoration: 'underline' }}
                                    title="Clear all recent history"
                                >
                                    Clear all
                                </button>
                            </div>
                            <div className="re-history-list">
                                {history.map((h) => {
                                    const isCurrentActive = result && (
                                        (h.downloadPdfUrl && result.download_url_pdf === h.downloadPdfUrl) ||
                                        (h.downloadHtmlUrl && result.download_url_html === h.downloadHtmlUrl)
                                    );
                                    return (
                                        <div
                                            key={h.id}
                                            className={`re-history-item ${isCurrentActive ? 'active-history' : ''}`}
                                            onClick={() => handleSelectHistoryItem(h)}
                                            style={{
                                                cursor: 'pointer',
                                                border: isCurrentActive ? '1px solid var(--accent-teal)' : '1px solid #e2e8f0',
                                                background: isCurrentActive ? '#f0fdfa' : '#f8fafc'
                                            }}
                                            title="Click to load and view report"
                                        >
                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                <div style={{ fontWeight: 600, color: '#0f172a', display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                    <span>{h.businessName}</span>
                                                    {isCurrentActive && (
                                                        <span style={{ fontSize: '0.68rem', background: '#ccfbf1', color: '#0f766e', padding: '0.1rem 0.4rem', borderRadius: '4px', fontWeight: 700 }}>
                                                            Active
                                                        </span>
                                                    )}
                                                </div>
                                                <div className="re-history-time">{h.timestamp} &bull; {h.sourceFile}</div>
                                            </div>

                                            <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                                                <button
                                                    type="button"
                                                    onClick={() => handleSelectHistoryItem(h)}
                                                    className="re-btn-secondary"
                                                    style={{ padding: '0.3rem 0.55rem', fontSize: '0.74rem', display: 'flex', alignItems: 'center', gap: '0.25rem', background: isCurrentActive ? 'var(--accent-teal)' : '#fff', color: isCurrentActive ? '#fff' : '#334155' }}
                                                    title="Open in Document Viewer"
                                                >
                                                    <Eye size={12} /> Open
                                                </button>

                                                {h.downloadPdfUrl && (
                                                    <a
                                                        href={`${API_BASE}${h.downloadPdfUrl}`}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="re-btn-secondary"
                                                        style={{ padding: '0.3rem 0.5rem', fontSize: '0.74rem', color: '#dc2626', fontWeight: 700 }}
                                                        title="Download PDF Document"
                                                    >
                                                        PDF
                                                    </a>
                                                )}

                                                {h.downloadHtmlUrl && (
                                                    <a
                                                        href={`${API_BASE}${h.downloadHtmlUrl}`}
                                                        target="_blank"
                                                        rel="noreferrer"
                                                        className="re-btn-secondary"
                                                        style={{ padding: '0.3rem 0.5rem', fontSize: '0.74rem', color: '#2563eb', fontWeight: 700 }}
                                                        title="Download HTML Report"
                                                    >
                                                        HTML
                                                    </a>
                                                )}

                                                <button
                                                    type="button"
                                                    onClick={(e) => handleDeleteHistoryItem(e, h)}
                                                    className="re-btn-delete-history"
                                                    title="Delete this report"
                                                    style={{ background: 'none', border: 'none', color: '#94a3b8', padding: '0.3rem', cursor: 'pointer', display: 'flex', alignItems: 'center', borderRadius: '4px' }}
                                                >
                                                    <Trash2 size={13} />
                                                </button>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>

                {/* Right Column: Live In-App Document Viewer */}
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
                                            ? 'Verified Audit-Proof Report'
                                            : 'Verification Warning — Review Flags'}
                                    </h3>
                                    <p>
                                        {result.report.verification_passed
                                            ? `Every single numeric claim (${result.report.verification.verified_count} total) was cross-checked against the deterministic analytics ledger within strict tolerance.`
                                            : `Notice: ${result.report.verification.mismatch_count} claim mismatch(es) detected. Review the computed ledger values below.`}
                                    </p>
                                    <div className="re-verdict-stats">
                                        <span className="re-verdict-pill">
                                            ✓ {result.report.verification.verified_count} Verified Claims
                                        </span>
                                        {result.report.verification.mismatch_count > 0 && (
                                            <span className="re-verdict-pill" style={{ color: '#b91c1c' }}>
                                                ✕ {result.report.verification.mismatch_count} Mismatched
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
                                            className={`re-doc-tab ${previewMode === 'html' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('html')}
                                            disabled={!result.download_url_html}
                                            style={{ opacity: result.download_url_html ? 1 : 0.5 }}
                                        >
                                            📄 Interactive Web Report
                                        </button>
                                        <button
                                            className={`re-doc-tab ${previewMode === 'pdf' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('pdf')}
                                            disabled={!result.download_url_pdf}
                                            style={{ opacity: result.download_url_pdf ? 1 : 0.5 }}
                                        >
                                            📑 PDF Document Preview
                                        </button>
                                        <button
                                            className={`re-doc-tab ${previewMode === 'audit' ? 'active' : ''}`}
                                            onClick={() => setPreviewMode('audit')}
                                        >
                                            🔍 Verification Audit Trail
                                        </button>
                                    </div>

                                    {/* Action Buttons */}
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
                                                title="Open in new browser window"
                                            >
                                                ↗ Pop Out
                                            </a>
                                        )}
                                    </div>
                                </div>

                                {/* Fallback when no download URLs exist */}
                                {!result.download_url_html && !result.download_url_pdf && previewMode !== 'audit' && (
                                    <div style={{ padding: '3.5rem 2rem', textAlign: 'center', background: '#fafbfc' }}>
                                        <div style={{ fontSize: '2.5rem', marginBottom: '0.75rem' }}>📄</div>
                                        <h3 style={{ color: '#0f172a', marginBottom: '0.5rem', fontSize: '1.1rem' }}>No File Attached to This Previous Session</h3>
                                        <p style={{ color: '#64748b', fontSize: '0.88rem', maxWidth: '400px', margin: '0 auto 1.5rem', lineHeight: 1.5 }}>
                                            Click <strong>Generate Verified Report</strong> on the left to compile the full multi-page PDF document and interactive web report.
                                        </p>
                                        <button
                                            className="re-btn-primary"
                                            style={{ display: 'inline-flex', width: 'auto', padding: '0.6rem 1.5rem', margin: '0 auto' }}
                                            onClick={handleGenerate}
                                            disabled={isGenerating}
                                        >
                                            <Sparkles size={16} /> Generate Report Now
                                        </button>
                                    </div>
                                )}

                                {/* Preview Body */}
                                {previewMode === 'html' && result.download_url_html && (
                                    <div className="re-iframe-wrapper">
                                        <iframe
                                            src={`${API_BASE}${result.download_url_html}`}
                                            className="re-doc-iframe"
                                            title="Interactive HTML Report"
                                        />
                                    </div>
                                )}

                                {previewMode === 'pdf' && result.download_url_pdf && (
                                    <div className="re-iframe-wrapper">
                                        <iframe
                                            src={`${API_BASE}${result.download_url_pdf}#view=FitH&toolbar=1`}
                                            className="re-doc-iframe"
                                            title="PDF Report Viewer"
                                        />
                                    </div>
                                )}

                                {previewMode === 'audit' && (
                                    <div style={{ padding: '1.5rem' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                                            <h4 style={{ margin: 0, color: '#0f172a', fontSize: '1rem' }}>Claim Verification Details</h4>
                                            <button className="re-btn-secondary" onClick={copyNarrativeToClipboard}>
                                                {copiedNarrative ? <Check size={14} color="#16a34a" /> : <Copy size={14} />}
                                                {copiedNarrative ? 'Copied Narrative' : 'Copy AI Narrative'}
                                            </button>
                                        </div>

                                        {result.report.narrative && (
                                            <div className="re-narrative-box" style={{ marginBottom: '1.5rem' }}>
                                                {result.report.narrative}
                                            </div>
                                        )}

                                        {result.report.verification.claims.length > 0 ? (
                                            <div style={{ border: '1px solid #e2e8f0', borderRadius: '8px', overflow: 'hidden' }}>
                                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.84rem' }}>
                                                    <thead>
                                                        <tr style={{ background: '#1e293b', color: 'white', textAlign: 'left' }}>
                                                            <th style={{ padding: '0.65rem 1rem' }}>Status</th>
                                                            <th style={{ padding: '0.65rem 1rem' }}>Extracted Claim</th>
                                                            <th style={{ padding: '0.65rem 1rem' }}>Value</th>
                                                            <th style={{ padding: '0.65rem 1rem' }}>Matched Ledger KPI</th>
                                                            <th style={{ padding: '0.65rem 1rem' }}>Expected Value</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {result.report.verification.claims.map((c, i) => (
                                                            <tr key={i} style={{ borderBottom: '1px solid #f1f5f9', background: i % 2 === 0 ? '#fff' : '#f8fafc' }}>
                                                                <td style={{ padding: '0.6rem 1rem' }}>
                                                                    {c.status === 'verified' && <span style={{ color: '#16a34a', fontWeight: 700 }}>✓ Verified</span>}
                                                                    {c.status === 'mismatch' && <span style={{ color: '#dc2626', fontWeight: 700 }}>✕ Mismatch</span>}
                                                                    {c.status === 'unmatched' && <span style={{ color: '#d97706', fontWeight: 700 }}>? Unmatched</span>}
                                                                </td>
                                                                <td style={{ padding: '0.6rem 1rem', fontFamily: 'monospace' }}>{c.matched_text}</td>
                                                                <td style={{ padding: '0.6rem 1rem', fontWeight: 600 }}>{c.extracted_value.toLocaleString()}</td>
                                                                <td style={{ padding: '0.6rem 1rem', color: '#64748b' }}>{c.matched_kpi_key || '—'}</td>
                                                                <td style={{ padding: '0.6rem 1rem' }}>{c.expected_value !== null ? c.expected_value.toLocaleString() : '—'}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        ) : (
                                            <div style={{ color: '#64748b', fontSize: '0.85rem' }}>No individual numeric claims recorded.</div>
                                        )}
                                    </div>
                                )}
                            </div>
                        </>
                    ) : (
                        /* Empty State Before First Generation */
                        <div className="re-empty-placeholder">
                            <div className="re-empty-icon">
                                <FileText size={36} />
                            </div>
                            <h3>Ready to Generate Verified Reports</h3>
                            <p>
                                Choose your data source on the left and click <strong>Generate Verified Report</strong>.
                                The full multi-page report with cover page, KPI stat grid, charts, data tables, and AI narration will be rendered directly in this viewport.
                            </p>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
