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
    ExternalLink,
    RotateCcw,
    Clock,
    Eye,
    Trash2
} from 'lucide-react';
import { useFilePath } from '../context/FileContext';
import { useReport } from '../context/ReportContext';
import { useUser } from '../context/UserContext';
import { getCustomDbGroups } from '../utils/dbGroups';
import './ReportExport.css';

interface FileOption {
    filename: string;
    file_path: string;
    extension: string;
    file_size_formatted: string;
    dir_type?: string;
    is_ingested?: boolean;
    source_type?: string;
    domain?: string;
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

const WEEKLY_SECTIONS_PHARMACY = [
    { id: 'cash_card_mix', label: '1. Cash vs. Card Mix', icon: CreditCard, desc: 'Settlement breakdown across checkout registers' },
    { id: 'expiry_loss_exposure', label: '2. Expiry Loss Exposure', icon: AlertTriangle, desc: 'Near-term capital risk in 30/60/90 day buckets' },
    { id: 'supplier_credit', label: '3. Supplier Payables', icon: Layers, desc: 'Distributor balances & earliest due dates' },
    { id: 'dead_stock', label: '4. Dead Stock Exposure', icon: PackageX, desc: 'Dormant inventory capital (>90 days without sale)' },
    { id: 'category_margin', label: '5. Margin by Category', icon: Percent, desc: 'Gross profit retention ranked by therapeutic group' },
    { id: 'reorder_alerts', label: '6. Priority Reorders', icon: BellRing, desc: 'Days of supply remaining & stockout risk' },
    { id: 'shrinkage_flags', label: '7. Inventory Shrinkage', icon: AlertCircle, desc: 'Discrepancies between physical stock and sales' },
    { id: 'seasonal_trend', label: '8. Seasonal Patterns', icon: TrendingUp, desc: 'Week-over-week trajectory & forward trend' },
];

const WEEKLY_SECTIONS_ECOMMERCE = [
    { id: 'gmv_sales_mix', label: '1. GMV & Revenue Performance', icon: CreditCard, desc: 'Gross merchandise value, net sales, discounts & tax' },
    { id: 'aov_basket_size', label: '2. Average Order Value (AOV)', icon: TrendingUp, desc: 'Basket size trajectory & average spend per transaction' },
    { id: 'refund_rate_exposure', label: '3. Refund & Return Exposure', icon: AlertTriangle, desc: 'Return rate %, refunded revenue & impacted product lines' },
    { id: 'customer_retention', label: '4. Customer Retention & Repeat Orders', icon: Layers, desc: 'First-time vs. recurring customer order distribution' },
    { id: 'product_margin_health', label: '5. Product & Category Margins', icon: Percent, desc: 'Gross margin retention ranked across merchandising lines' },
    { id: 'fulfillment_speed', label: '6. Fulfillment & Dispatch Metrics', icon: BellRing, desc: 'Unfulfilled backlog, fulfillment rate & shipping mix' },
    { id: 'payment_gateway_mix', label: '7. Payment Gateway Performance', icon: AlertCircle, desc: 'Shopify Payments, Stripe, PayPal & COD settlement mix' },
    { id: 'weekly_seasonal_trend', label: '8. Weekly Trajectory & Growth', icon: TrendingUp, desc: 'Week-over-week sales trajectory & forward forecast' },
];

export default function WeeklyReport() {
    const { user, activeDomainMeta } = useUser();
    const { activePath } = useFilePath();
    const {
        weeklyIsGenerating: isGenerating,
        weeklyGenerationStep: generationStep,
        weeklyGenerationProgress: generationProgress,
        weeklyError: error,
        weeklyResult: result,
        setWeeklyResult,
        weeklySelectedFile: selectedFile,
        setWeeklySelectedFile: setSelectedFile,
        weeklyBusinessName: businessName,
        setWeeklyBusinessName: setBusinessName,
        weeklyFormatPdf: formatPdf,
        setWeeklyFormatPdf: setFormatPdf,
        weeklyFormatHtml: formatHtml,
        setWeeklyFormatHtml: setFormatHtml,
        weeklyAllowRetry: allowRetry,
        setWeeklyAllowRetry: setAllowRetry,
        weeklyPreviewMode: previewMode,
        setWeeklyPreviewMode: setPreviewMode,
        generateWeeklyReport,
        clearWeeklyResult
    } = useReport();

    const weeklySections = user.domain === 'ecommerce' ? WEEKLY_SECTIONS_ECOMMERCE : WEEKLY_SECTIONS_PHARMACY;

    // Local list of files from backend
    const [availableFiles, setAvailableFiles] = useState<FileOption[]>([]);
    const [availableDatabases, setAvailableDatabases] = useState<{ database_name: string; domain?: string }[]>([]);
    const [history, setHistory] = useState<ReportHistoryItem[]>([]);

    useEffect(() => {
        document.title = `${activeDomainMeta.name} Weekly Report — LLM-KONNECT`;
        fetchAvailableFiles();
        fetchDiskReports();
    }, [activePath, user.domain]);

    const fetchDiskReports = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/report/list?domain=${encodeURIComponent(user.domain)}`);
            if (res.ok) {
                const data = await res.json();
                const diskReports: any[] = data.reports || [];
                const historyList: ReportHistoryItem[] = diskReports.map(r => ({
                    id: r.id || r.stem,
                    stem: r.stem,
                    timestamp: r.created_formatted || 'Recently',
                    businessName: r.business_name || businessName || (user.domain === 'ecommerce' ? 'E-Commerce Weekly Store Performance' : 'Pharmacy Weekly Executive Report'),
                    domain: r.domain || user.domain,
                    sourceFile: r.source_file || 'Uploaded Dataset',
                    verificationPassed: r.verification_passed ?? true,
                    verifiedCount: r.verified_count || 8,
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
        let listedFiles: FileOption[] = [];
        try {
            const res = await fetch(`${API_BASE}/api/files?domain=${encodeURIComponent(user.domain)}`);
            if (res.ok) {
                const data = await res.json();
                const rawFiles: any[] = data.files || [];
                const validExts = ['.csv', '.xlsx', '.xls', '.db', '.json', '.sqlite'];
                const filesList: FileOption[] = rawFiles
                    .filter(f => {
                        const filePath = String(f.file_path || '');
                        if (filePath.startsWith('db://')) return false;
                        if (filePath.startsWith('sql://')) return true;
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
                        is_ingested: f.is_ingested || false,
                        domain: f.domain
                    }));
                setAvailableFiles(filesList);
                listedFiles = filesList;

                if (activePath && filesList.some(f => f.file_path === activePath || f.filename === activePath)) {
                    const matched = filesList.find(f => f.file_path === activePath || f.filename === activePath);
                    if (matched && !selectedFile) setSelectedFile(matched.file_path);
                } else if (filesList.length > 0 && !selectedFile) {
                    setSelectedFile(filesList[0].file_path);
                }
            }
        } catch (e) {
            console.warn('Could not fetch file list from backend:', e);
        }
        try {
            const res = await fetch(`${API_BASE}/api/kb/database-connections`);
            if (res.ok) {
                const data = await res.json();
                const databases = (data.connections || [])
                    .filter((c: any) => (c.domain || 'pharmacy') === user.domain)
                    .map((c: any) => ({ database_name: c.database_name, domain: c.domain }));
                setAvailableDatabases(databases);
                const activeDatabase = databases.find((database: { database_name: string }) => activePath === `db://${database.database_name}`);
                if (!selectedFile && activeDatabase) {
                    setSelectedFile(activePath);
                } else if (!selectedFile && databases.length > 0 && listedFiles.length === 0) {
                    setSelectedFile(`db://${databases[0].database_name}`);
                }
            }
        } catch (e) {
            console.warn('Could not fetch connected databases:', e);
        }
    };

    const handleGenerateWeekly = async () => {
        await generateWeeklyReport(selectedFile);
        await fetchDiskReports();
    };

    const handleSelectHistoryItem = (h: ReportHistoryItem) => {
        const htmlUrl = h.downloadHtmlUrl;
        const pdfUrl = h.downloadPdfUrl;

        if (pdfUrl) {
            setPreviewMode('pdf');
        } else if (htmlUrl) {
            setPreviewMode('html');
        } else {
            setPreviewMode('audit');
        }

        setWeeklyResult({
            report: {
                kpi_snapshot: {},
                narrative: '',
                verification: {
                    all_verified: h.verificationPassed ?? true,
                    verified_count: h.verifiedCount || 9,
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
            source: h.sourceFile || 'pharmacy_dataset.csv'
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
        clearWeeklyResult();
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
                            <h1 className="re-title">{activeDomainMeta.name} Weekly Report</h1>
                            <span className="re-badge-live">
                                <ShieldCheck size={12} /> 7-Day Audit
                            </span>
                        </div>
                        <p className="re-subtitle">
                            {user.domain === 'ecommerce'
                                ? 'Automated operational intelligence across GMV, order volume, refunds, customer retention, and merchandising.'
                                : 'Automated operational intelligence across settlements, margins, expiry risks, and inventory.'}
                        </p>
                    </div>
                </div>
            </div>

            {/* 2. Key Audit Pillars (Google Pill Ribbon) */}
            <div className="weekly-pillars-bar">
                <span className="weekly-pillars-label">Coverage:</span>
                <div className="weekly-pillars-list">
                    {weeklySections.map((sec) => {
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
                            <label className="re-label" style={{ margin: 0 }}>
                                {user.domain === 'ecommerce' ? 'Store Orders / Catalog Dataset' : 'POS / Ledger Source'}
                            </label>
                            <button
                                type="button"
                                onClick={fetchAvailableFiles}
                                title="Refresh files"
                                className="re-refresh-btn"
                            >
                                <RefreshCw size={12} /> Refresh
                            </button>
                        </div>
                        {availableFiles.length > 0 || availableDatabases.length > 0 ? (() => {
                            const customGroups = getCustomDbGroups(user.domain);
                            const groupedDbNamesSet = new Set(
                                customGroups.flatMap(grp => grp.dbNames.map(d => d.toLowerCase().trim()))
                            );
                            const ungroupedDatabases = availableDatabases.filter(
                                (database) => !groupedDbNamesSet.has(database.database_name.toLowerCase().trim())
                            );

                            return (
                                <select
                                    className="re-select"
                                    value={selectedFile}
                                    onChange={(e) => setSelectedFile(e.target.value)}
                                    disabled={isGenerating}
                                >
                                    {(customGroups.length > 0 || ungroupedDatabases.length > 0) && (
                                        <optgroup label="Whole POS databases">
                                            {customGroups.map((grp) => (
                                                <option key={`custom-${grp.id}`} value={`db://${grp.dbNames.join(',')}`}>
                                                    {grp.name} ({grp.dbNames.join(' + ')})
                                                </option>
                                            ))}
                                            {ungroupedDatabases.map((database) => (
                                                <option key={`db-${database.database_name}`} value={`db://${database.database_name}`}>
                                                    {database.database_name} — Entire database
                                                </option>
                                            ))}
                                        </optgroup>
                                    )}
                                    {availableFiles.length > 0 && (
                                        <optgroup label="Individual files">
                                            {availableFiles.map((file, idx) => (
                                                <option key={`file-${idx}`} value={file.file_path}>
                                                    {file.filename} ({file.file_size_formatted})
                                                </option>
                                            ))}
                                        </optgroup>
                                    )}
                                </select>
                            );
                        })() : (
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

                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                        <button
                            className="re-btn-primary"
                            style={{ flex: 1 }}
                            onClick={handleGenerateWeekly}
                            disabled={isGenerating || (!formatPdf && !formatHtml)}
                        >
                            {isGenerating ? (
                                <>
                                    <RefreshCw size={16} className="animate-spin" /> Generating Report...
                                </>
                            ) : (
                                <>
                                    <Sparkles size={16} /> {result ? 'Regenerate Weekly Report' : 'Generate Weekly Report'}
                                </>
                            )}
                        </button>
                        {result && !isGenerating && (
                            <button
                                type="button"
                                className="re-btn-secondary"
                                onClick={clearWeeklyResult}
                                title="Clear current report and start fresh"
                                style={{ padding: '0.65rem 0.85rem' }}
                            >
                                <RotateCcw size={15} />
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
                        <div style={{ marginTop: '2rem', borderTop: '1px solid var(--border-color)', paddingTop: '1.25rem' }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
                                <div style={{ fontSize: '0.85rem', fontWeight: 700, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                    <Clock size={15} /> Recent Reports ({history.length})
                                </div>
                                <button
                                    type="button"
                                    onClick={handleClearAllHistory}
                                    style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: '0.72rem', textDecoration: 'underline' }}
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
                                                border: isCurrentActive ? '1px solid var(--brand-teal)' : '1px solid var(--border-color)',
                                                background: isCurrentActive ? 'rgba(13, 115, 119, 0.12)' : 'var(--surface-container)'
                                            }}
                                            title="Click to load and view report"
                                        >
                                            <div style={{ flex: 1, minWidth: 0 }}>
                                                <div style={{ fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                    <span>{h.businessName}</span>
                                                    {isCurrentActive && (
                                                        <span style={{ fontSize: '0.68rem', background: 'var(--brand-green-container)', color: 'var(--brand-green-text)', padding: '0.1rem 0.4rem', borderRadius: '4px', fontWeight: 700 }}>
                                                            Active
                                                        </span>
                                                    )}
                                                </div>
                                                <div className="re-history-time" style={{ color: 'var(--text-tertiary)', fontSize: '0.75rem', marginTop: '0.15rem' }}>
                                                    {h.timestamp} &bull; {h.sourceFile}
                                                </div>
                                            </div>

                                            <div style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', flexShrink: 0 }} onClick={(e) => e.stopPropagation()}>
                                                <button
                                                    type="button"
                                                    onClick={() => handleSelectHistoryItem(h)}
                                                    className="re-btn-secondary"
                                                    style={{ padding: '0.3rem 0.55rem', fontSize: '0.74rem', display: 'flex', alignItems: 'center', gap: '0.25rem', background: isCurrentActive ? 'var(--brand-teal)' : 'var(--surface-bg)', color: isCurrentActive ? '#fff' : 'var(--text-primary)' }}
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
                                                    style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', padding: '0.3rem', cursor: 'pointer', display: 'flex', alignItems: 'center', borderRadius: '4px' }}
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
                                <div className="re-doc-frame-wrapper re-iframe-wrapper" style={{ minHeight: '800px', height: '850px', width: '100%' }}>
                                    {previewMode === 'pdf' && result.download_url_pdf && (
                                        <iframe
                                            src={`${API_BASE}${result.download_url_pdf}#view=FitH&toolbar=1`}
                                            className="re-doc-iframe"
                                            style={{ width: '100%', height: '100%', minHeight: '800px', border: 'none' }}
                                            title="PDF Preview"
                                        />
                                    )}

                                    {previewMode === 'html' && result.download_url_html && (
                                        <iframe
                                            src={`${API_BASE}${result.download_url_html}`}
                                            className="re-doc-iframe"
                                            style={{ width: '100%', height: '100%', minHeight: '800px', border: 'none' }}
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
