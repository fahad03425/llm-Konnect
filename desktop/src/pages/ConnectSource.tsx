// ============================================================
//  ConnectSource.tsx — 6-step data-connection wizard
//  Steps: Upload → Preview → Mapping → Normalize → Validate → Ingest
//
//  STEP STATE MACHINE (step is always a clean integer 0-6):
//    0 = Upload form
//    1 = Preview (load & confirm)
//    2 = Mapping (auto-loads, user confirms)
//    3 = Normalize
//    4 = Validate
//    5 = Ingest
//    6 = Complete
// ============================================================
import { useState, useEffect, Component, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
    Upload, Eye, GitMerge, Wrench, ShieldCheck, Database,
    AlertCircle, CheckCircle, AlertTriangle, ArrowRight, RefreshCw,
    Zap, Play, Folder, HardDrive, FileText
} from 'lucide-react';
import { StepIndicator } from '../components/connect/StepIndicator';
import { UploadZone } from '../components/connect/UploadZone';
import { PreviewTable } from '../components/connect/PreviewTable';
import { MappingTable } from '../components/connect/MappingTable';
import { KBStatus } from '../components/connect/KBStatus';
import { useFilePath } from '../context/FileContext';
import { useUser } from '../context/UserContext';
import '../Connect.css';

// ---- Types ----
type Verdict = 'usable' | 'usable_with_warnings' | 'not_usable';
type SourceType = 'file' | 'sql' | 'watcher';

interface PreviewData {
    columns: string[];
    sample_rows: (string | number | null)[][];
    total_rows: number;
}

interface ValidateResult {
    verdict: Verdict;
    problems: string[];
    null_counts: Record<string, number>;
}

interface KBStats {
    total_chunks: number;
    collection_name: string;
}

type StepState = { loading: boolean; error: string | null };
const idle = (): StepState => ({ loading: false, error: null });

// Helper delay for smooth auto-transition between steps
const delay = (ms: number) => new Promise(res => setTimeout(res, ms));

// ============================================================
//  Error Boundary — catches any render crash and shows a card
// ============================================================
class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean; message: string }> {
    constructor(props: { children: ReactNode }) {
        super(props);
        this.state = { hasError: false, message: '' };
    }
    static getDerivedStateFromError(err: Error) {
        return { hasError: true, message: err.message };
    }
    render() {
        if (this.state.hasError) {
            return (
                <div className="error-card" style={{ margin: '2rem' }}>
                    <AlertCircle size={20} />
                    <div>
                        <strong>Something went wrong.</strong>
                        <div style={{ marginTop: '0.25rem', fontFamily: 'monospace', fontSize: '0.82rem' }}>
                            {this.state.message}
                        </div>
                        <button
                            className="btn-secondary"
                            style={{ marginTop: '0.75rem' }}
                            onClick={() => this.setState({ hasError: false, message: '' })}
                        >
                            Try Again
                        </button>
                    </div>
                </div>
            );
        }
        return this.props.children;
    }
}

// ============================================================
//  Reusable inline error card with Retry button
// ============================================================
const ErrorCard = ({ msg, onRetry }: { msg: string; onRetry: () => void }) => (
    <div className="error-card">
        <AlertCircle size={18} style={{ flexShrink: 0 }} />
        <div style={{ flex: 1 }}>{msg}</div>
        <button
            className="btn-secondary"
            onClick={onRetry}
            style={{ padding: '0.4rem 0.875rem', fontSize: '0.82rem', flexShrink: 0 }}
        >
            <RefreshCw size={13} /> Retry
        </button>
    </div>
);

// ============================================================
//  ConnectSource — main wizard component
// ============================================================
export default function ConnectSource() {
    const navigate = useNavigate();
    const { setActivePath } = useFilePath();

    const { user, activeDomainMeta, openSettings } = useUser();
    const domain = user.domain;
    const [sourceType, setSourceType] = useState<SourceType>('file');

    // ── Execution Mode: Manual vs Automatic ────────────────────────
    const [autoProceed, setAutoProceed] = useState(false);

    // ── Wizard step (0-6, always an integer) ──────────────────────
    const [step, setStep] = useState(0);

    // ── Data carried across all steps ─────────────────────────────
    const [file, setFile] = useState<File | null>(null);
    const [filePath, setFilePath] = useState('');       // set in step 1, updated in step 4
    const [sheetName, setSheetName] = useState<string | null>(null);
    const [sheets, setSheets] = useState<string[]>([]);
    const [mapping, setMapping] = useState<Record<string, string>>({});  // *** pass to all steps ***
    const [previewData, setPreviewData] = useState<PreviewData | null>(null);
    const [validateResult, setValidateResult] = useState<ValidateResult | null>(null);
    const [ingestMsg, setIngestMsg] = useState('');
    const [kbStats, setKbStats] = useState<KBStats | null>(null);

    // ── SQL Connection State ───────────────────────────────────────
    const [dbType, setDbType] = useState('sqlite');
    const [connString, setConnString] = useState('');
    const [sqlQuery, setSqlQuery] = useState('');

    // ── Folder Watcher State ───────────────────────────────────────
    const [watchDir, setWatchDir] = useState('');
    const [pendingFiles, setPendingFiles] = useState<string[]>([]);

    // ── Per-step loading / error ───────────────────────────────────
    const [uploadSt, setUploadSt] = useState<StepState>(idle());
    const [previewSt, setPreviewSt] = useState<StepState>(idle());
    const [mappingSt, setMappingSt] = useState<StepState>(idle());
    const [normSt, setNormSt] = useState<StepState>(idle());
    const [valSt, setValSt] = useState<StepState>(idle());
    const [ingestSt, setIngestSt] = useState<StepState>(idle());

    // ── Load KB stats on mount ─────────────────────────────────────
    useEffect(() => {
        document.title = 'Connect Source — LLM-KONNECT';
        void fetchKBStats();
    }, []);

    const fetchKBStats = async () => {
        try {
            const res = await fetch('/api/kb/stats');
            if (res.ok) setKbStats(await res.json());
        } catch { /* non-critical */ }
    };

    // ── Helper: reset file and restart wizard ──────────────────────
    const resetFile = (f: File | null) => {
        setFile(f);
        setFilePath('');
        setSheets([]);
        setSheetName(null);
        setPreviewData(null);
        setMapping({});
        setValidateResult(null);
        setIngestMsg('');
        setUploadSt(idle()); setPreviewSt(idle()); setMappingSt(idle());
        setNormSt(idle()); setValSt(idle()); setIngestSt(idle());
        setStep(0);
    };

    // ==============================================================
    //  STEP 1 — UPLOAD FILE
    // ==============================================================
    const doUpload = async (overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        if (!file) return;
        setUploadSt({ loading: true, error: null });

        const form = new FormData();
        form.append('file', file);

        try {
            const res = await fetch('/api/sources/upload', { method: 'POST', body: form });
            if (!res.ok) throw new Error(await res.text());

            const data = await res.json();
            const fp: string = data.file_path;
            setFilePath(fp);

            let firstSheet: string | null = null;
            if (/\.(xlsx|xls)$/i.test(file.name)) {
                try {
                    const sRes = await fetch(
                        `/api/sources/sheets?file_path=${encodeURIComponent(fp)}`,
                        { method: 'POST' }
                    );
                    if (sRes.ok) {
                        const sData = await sRes.json();
                        const sheetList: string[] = sData.sheets ?? [];
                        setSheets(sheetList);
                        if (sheetList.length > 0) {
                            firstSheet = sheetList[0];
                            setSheetName(firstSheet);
                        }
                    }
                } catch { /* non-critical */ }
            }

            setUploadSt(idle());
            setStep(1);

            if (isAuto) {
                await delay(400);
                await doPreview(fp, firstSheet, isAuto);
            }
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'Upload failed.') });
        }
    };

    // ==============================================================
    //  STEP 1B — CONNECT SQL DATABASE
    // ==============================================================
    const doConnectSQL = async (overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        if (!connString || !sqlQuery) return;
        setUploadSt({ loading: true, error: null });

        try {
            const res = await fetch('/api/sources/sql/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_string: connString,
                    db_type: dbType,
                    table_or_query: sqlQuery,
                    domain: domain
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            setPreviewData({
                columns: data.columns || [],
                sample_rows: data.data ? data.data.map((r: any) => (data.columns || []).map((c: string) => r[c])) : [],
                total_rows: data.data ? data.data.length : 0
            });

            const proposedMap: Record<string, string> = {};
            if (data.mapping_proposal && data.mapping_proposal.suggestions) {
                data.mapping_proposal.suggestions.forEach((s: any) => {
                    if (s.canonical_field) proposedMap[s.source_column] = s.canonical_field;
                });
            }
            if (Object.keys(proposedMap).length > 0) setMapping(proposedMap);

            setUploadSt(idle());
            setStep(1);

            if (isAuto) {
                await delay(400);
                await doMapping(connString, proposedMap, null, isAuto);
            }
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'SQL connection failed.') });
        }
    };

    // ==============================================================
    //  STEP 1C — FOLDER WATCHER
    // ==============================================================
    const doCheckWatcher = async () => {
        if (!watchDir) return;
        setUploadSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/watcher/list', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ watch_dir: watchDir, domain: domain })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            const files: string[] = data.pending_files || [];
            setPendingFiles(files);
            if (files.length > 0) {
                setFilePath(files[0]);
            }
            setUploadSt(idle());
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'Directory scan failed.') });
        }
    };

    // ==============================================================
    //  STEP 2 — PREVIEW
    // ==============================================================
    const doPreview = async (targetFp?: string, targetSheet?: string | null, overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        const currentFp = targetFp || filePath;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;

        setPreviewSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: currentFp, sheet_name: currentSheet || null, domain: domain })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            const sampleRows = data.data ? data.data.map((row: any) => (data.columns || []).map((col: string) => row[col])) : [];

            setPreviewData({
                columns: data.columns || [],
                sample_rows: sampleRows,
                total_rows: data.data ? data.data.length : 0
            });

            const proposedMap: Record<string, string> = {};
            if (data.mapping_proposal && data.mapping_proposal.suggestions) {
                data.mapping_proposal.suggestions.forEach((s: any) => {
                    if (s.canonical_field) proposedMap[s.source_column] = s.canonical_field;
                });
            }
            if (Object.keys(proposedMap).length > 0) {
                setMapping(proposedMap);
            }

            setPreviewSt(idle());

            if (isAuto) {
                await delay(400);
                await doMapping(currentFp, proposedMap, currentSheet, isAuto);
            }
        } catch (e: unknown) {
            setPreviewSt({ loading: false, error: String((e as Error).message ?? 'Preview failed.') });
        }
    };

    // ==============================================================
    //  STEP 3 — MAPPING CONFIRM
    // ==============================================================
    const doMapping = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null, overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;

        setMappingSt({ loading: true, error: null });
        setStep(2);
        try {
            const res = await fetch('/api/sources/mapping/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: currentFp,
                    mapping: currentMap,
                    domain: domain,
                    sheet_name: currentSheet || null,
                    table_or_query: null,
                    keep_extras: true,
                    save_profile: true
                })
            });
            if (!res.ok) throw new Error(await res.text());
            setMappingSt(idle());
            setStep(3);

            if (isAuto) {
                await delay(400);
                await doNormalize(currentFp, currentMap, currentSheet, isAuto);
            }
        } catch (e: unknown) {
            setMappingSt({ loading: false, error: String((e as Error).message ?? 'Mapping failed.') });
        }
    };

    // ==============================================================
    //  STEP 4 — NORMALIZE
    // ==============================================================
    const doNormalize = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null, overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;

        setNormSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/normalize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: currentFp,
                    domain: domain,
                    mapping: currentMap,
                    sheet_name: currentSheet || null,
                    table_or_query: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            const normFp = data.file_path || currentFp;
            setFilePath(normFp);
            setNormSt(idle());
            setStep(4);

            if (isAuto) {
                await delay(400);
                await doValidate(normFp, currentMap, currentSheet, isAuto);
            }
        } catch (e: unknown) {
            setNormSt({ loading: false, error: String((e as Error).message ?? 'Normalize failed.') });
        }
    };

    // ==============================================================
    //  STEP 5 — VALIDATE
    // ==============================================================
    const doValidate = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null, overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;

        setValSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/validate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: currentFp,
                    mapping: currentMap,
                    domain: domain,
                    table_kind: 'auto',
                    sheet_name: currentSheet || null,
                    table_or_query: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            const problemStrings = data.problems?.map((p: any) => typeof p === 'string' ? p : p.description) || [];

            setValidateResult({
                verdict: data.verdict,
                problems: problemStrings,
                null_counts: data.null_counts || {}
            });
            setValSt(idle());

            if (data.verdict === 'usable' || data.verdict === 'usable_with_warnings') {
                setStep(5);
                if (isAuto) {
                    await delay(400);
                    await doIngest(currentFp, currentMap, currentSheet);
                }
            }
        } catch (e: unknown) {
            setValSt({ loading: false, error: String((e as Error).message ?? 'Validation failed.') });
        }
    };

    // ==============================================================
    //  STEP 6 — INGEST
    // ==============================================================
    const doIngest = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null) => {
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;

        setIngestSt({ loading: true, error: null });
        setStep(5);
        try {
            const res = await fetch('/api/kb/ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: currentFp,
                    domain: domain,
                    strategy: 'row',
                    mapping: currentMap,
                    sheet_name: currentSheet || null,
                    table_or_query: null,
                    merge_key: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const raw = await res.text();
            let msg = raw;
            try {
                const parsed = JSON.parse(raw);
                if (typeof parsed === 'object' && parsed !== null) {
                    msg = parsed.message || parsed.detail || JSON.stringify(parsed);
                } else {
                    msg = String(parsed);
                }
            } catch { /* plain string */ }
            setIngestMsg(String(msg));
            setActivePath(currentFp);
            setIngestSt(idle());
            setStep(6);
            void fetchKBStats();
        } catch (e: unknown) {
            setIngestSt({ loading: false, error: String((e as Error).message ?? 'Ingest failed.') });
        }
    };

    // ── Computed: indicator uses clean 0-6 integer ────────────────
    const indicatorStep = Math.min(step, 6);

    // ==============================================================
    //  RENDER
    // ==============================================================
    return (
        <ErrorBoundary>
            <div className="connect-page">

                {/* ── Header with Execution Mode Selector & Active Niche Info ── */}
                <div className="connect-page-header">
                    <div>
                        <h2>Connect Your Data Source</h2>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', marginTop: '0.25rem' }}>
                            <p style={{ margin: 0 }}>Connect data sources (Files, SQL, Folder Watcher) to your knowledge base.</p>
                            <span
                                style={{
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: '0.35rem',
                                    background: 'rgba(16, 185, 129, 0.12)',
                                    color: 'var(--accent-teal)',
                                    border: '1px solid rgba(16, 185, 129, 0.25)',
                                    padding: '0.2rem 0.65rem',
                                    borderRadius: '12px',
                                    fontSize: '0.78rem',
                                    fontWeight: 600
                                }}
                            >
                                <span>{activeDomainMeta.icon}</span> {activeDomainMeta.name} Mode
                            </span>
                            <button
                                type="button"
                                onClick={openSettings}
                                style={{
                                    background: 'none',
                                    border: 'none',
                                    color: 'var(--text-secondary)',
                                    fontSize: '0.76rem',
                                    textDecoration: 'underline',
                                    cursor: 'pointer',
                                    padding: 0
                                }}
                            >
                                Change Domain
                            </button>
                        </div>
                    </div>

                    <div className="proceed-mode-container">
                        <span className="mode-label">Execution Mode</span>
                        <div className="proceed-mode-toggle">
                            <button
                                type="button"
                                className={`mode-btn ${!autoProceed ? 'active' : ''}`}
                                onClick={() => setAutoProceed(false)}
                            >
                                <Play size={13} /> Manual Proceed
                            </button>
                            <button
                                type="button"
                                className={`mode-btn ${autoProceed ? 'active' : ''}`}
                                onClick={() => setAutoProceed(true)}
                            >
                                <Zap size={13} /> Automatic Proceed
                            </button>
                        </div>
                    </div>
                </div>

                {/* ── Auto proceed active banner ──────────────── */}
                {autoProceed && (
                    <div className="auto-proceed-banner">
                        <Zap size={16} />
                        <span><strong>Automatic Mode Active:</strong> Pipeline steps for <strong>{domain.toUpperCase()}</strong> will execute automatically in sequence.</span>
                    </div>
                )}

                {/* ── Progress indicator ─────────────────────── */}
                <StepIndicator currentStep={indicatorStep} />

                {/* ════════════════════════════════════════════
                    STEP 1 — CONNECT DATA SOURCE
                ════════════════════════════════════════════ */}
                <div className="wizard-card">
                    {/* Source Connection Tabs */}
                    <div className="source-tabs">
                        <button
                            type="button"
                            className={`source-tab-btn ${sourceType === 'file' ? 'active' : ''}`}
                            onClick={() => setSourceType('file')}
                        >
                            <FileText size={14} /> File Upload (CSV / Excel / JSON)
                        </button>
                        <button
                            type="button"
                            className={`source-tab-btn ${sourceType === 'sql' ? 'active' : ''}`}
                            onClick={() => setSourceType('sql')}
                        >
                            <HardDrive size={14} /> SQL Database Connection
                        </button>
                        <button
                            type="button"
                            className={`source-tab-btn ${sourceType === 'watcher' ? 'active' : ''}`}
                            onClick={() => setSourceType('watcher')}
                        >
                            <Folder size={14} /> Folder Auto-Sync Watcher
                        </button>
                    </div>

                    <div className="wizard-card-title"><Upload size={16} /> Step 1 — Connect Source ({domain.replace('_', ' ').toUpperCase()})</div>

                    {/* SOURCE 1: FILE UPLOAD */}
                    {sourceType === 'file' && (
                        <>
                            <UploadZone file={file} onFileChange={resetFile} />

                            {sheets.length > 1 && (
                                <div className="sheet-select-wrap">
                                    <label>Select worksheet:</label>
                                    <select
                                        className="sheet-select"
                                        value={sheetName ?? ''}
                                        onChange={e => setSheetName(e.target.value)}
                                    >
                                        {sheets.map(s => <option key={s} value={s}>{s}</option>)}
                                    </select>
                                </div>
                            )}

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={() => doUpload()} />
                                </div>
                            )}

                            {step === 0 && (
                                <div className="btn-actions">
                                    <button
                                        className="btn-primary"
                                        onClick={() => doUpload()}
                                        disabled={!file || uploadSt.loading}
                                    >
                                        {uploadSt.loading
                                            ? <><span className="spinner" /> Uploading…</>
                                            : autoProceed
                                                ? <><Zap size={15} /> Upload &amp; Auto Process</>
                                                : <><Upload size={15} /> Upload &amp; Analyse</>}
                                    </button>
                                </div>
                            )}
                        </>
                    )}

                    {/* SOURCE 2: SQL DATABASE */}
                    {sourceType === 'sql' && (
                        <div style={{ marginTop: '0.5rem' }}>
                            <div className="sql-form-group">
                                <label>Database Engine:</label>
                                <select
                                    className="sql-form-input"
                                    value={dbType}
                                    onChange={e => setDbType(e.target.value)}
                                >
                                    <option value="sqlite">SQLite (.db / .sqlite)</option>
                                    <option value="postgresql">PostgreSQL</option>
                                    <option value="mysql">MySQL / MariaDB</option>
                                    <option value="mssql">MS SQL Server</option>
                                </select>
                            </div>

                            <div className="sql-form-group">
                                <label>Connection String or File Path:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder={dbType === 'sqlite' ? 'C:/data/pharmacy.db' : 'postgresql://user:pass@localhost:5432/mydb'}
                                    value={connString}
                                    onChange={e => setConnString(e.target.value)}
                                />
                            </div>

                            <div className="sql-form-group">
                                <label>Table Name or SQL Query:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder="SELECT * FROM transactions"
                                    value={sqlQuery}
                                    onChange={e => setSqlQuery(e.target.value)}
                                />
                            </div>

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={() => doConnectSQL()} />
                                </div>
                            )}

                            {step === 0 && (
                                <div className="btn-actions">
                                    <button
                                        className="btn-primary"
                                        onClick={() => doConnectSQL()}
                                        disabled={!connString || !sqlQuery || uploadSt.loading}
                                    >
                                        {uploadSt.loading
                                            ? <><span className="spinner" /> Connecting…</>
                                            : autoProceed
                                                ? <><Zap size={15} /> Connect SQL &amp; Auto Process</>
                                                : <><HardDrive size={15} /> Connect &amp; Fetch Preview</>}
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {/* SOURCE 3: FOLDER WATCHER */}
                    {sourceType === 'watcher' && (
                        <div style={{ marginTop: '0.5rem' }}>
                            <div className="sql-form-group">
                                <label>Automated Export Directory Path:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder="C:/POS_Exports/"
                                    value={watchDir}
                                    onChange={e => setWatchDir(e.target.value)}
                                />
                            </div>

                            <div className="btn-actions">
                                <button
                                    className="btn-secondary"
                                    onClick={doCheckWatcher}
                                    disabled={!watchDir || uploadSt.loading}
                                >
                                    <Folder size={15} /> Scan Directory
                                </button>
                            </div>

                            {pendingFiles.length > 0 && (
                                <div style={{ marginTop: '1rem' }}>
                                    <label style={{ fontSize: '0.82rem', fontWeight: 600 }}>Detected Pending Files:</label>
                                    <ul style={{ fontSize: '0.85rem', color: '#374151', margin: '0.5rem 0' }}>
                                        {pendingFiles.map(f => <li key={f}>📄 {f}</li>)}
                                    </ul>
                                </div>
                            )}

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={doCheckWatcher} />
                                </div>
                            )}

                            {step === 0 && pendingFiles.length > 0 && (
                                <div className="btn-actions" style={{ marginTop: '1rem' }}>
                                    <button
                                        className="btn-primary"
                                        onClick={() => doPreview(pendingFiles[0])}
                                    >
                                        <ArrowRight size={15} /> Process Latest File ({pendingFiles[0].split('/').pop()})
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {/* Success confirmation */}
                    {step >= 1 && (
                        <div className="info-card" style={{ marginTop: '1rem' }}>
                            <CheckCircle size={16} />
                            Source connected for domain: <strong>{domain.toUpperCase()}</strong>.
                        </div>
                    )}
                </div>

                {/* ════════════════════════════════════════════
                    STEP 2 — PREVIEW
                ════════════════════════════════════════════ */}
                {step >= 1 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Eye size={16} /> Step 2 — Preview Data ({domain.toUpperCase()})</div>

                        {previewSt.error && <ErrorCard msg={previewSt.error} onRetry={() => doPreview()} />}

                        {!previewData && !previewSt.loading && step === 1 && (
                            <div className="btn-actions">
                                <button className="btn-primary" onClick={() => doPreview()}>
                                    <Eye size={15} /> Load Preview
                                </button>
                            </div>
                        )}

                        {previewSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Loading preview…
                            </div>
                        )}

                        {previewData && step === 1 && (
                            <>
                                <PreviewTable
                                    columns={previewData.columns}
                                    sampleRows={previewData.sample_rows}
                                    totalRows={previewData.total_rows}
                                />
                                <div className="btn-actions">
                                    <button className="btn-primary" onClick={() => doMapping()}>
                                        {autoProceed ? <Zap size={15} /> : <ArrowRight size={15} />} Confirm Preview &amp; Load Mapping
                                    </button>
                                </div>
                            </>
                        )}

                        {step >= 2 && (
                            <div className="info-card">
                                <CheckCircle size={16} /> Preview confirmed. Column mapping loaded for {domain.toUpperCase()}.
                            </div>
                        )}
                    </div>
                )}

                {/* ════════════════════════════════════════════
                    STEP 3 — MAPPING
                ════════════════════════════════════════════ */}
                {step >= 2 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><GitMerge size={16} /> Step 3 — Column Mapping ({domain.toUpperCase()})</div>

                        {mappingSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Detecting column mapping for {domain}…
                            </div>
                        )}

                        {mappingSt.error && <ErrorCard msg={mappingSt.error} onRetry={() => doMapping()} />}

                        {!mappingSt.loading && Object.keys(mapping).length > 0 && (
                            <>
                                <p style={{ fontSize: '0.875rem', color: '#6B7280', marginBottom: '1rem' }}>
                                    The system detected the following column mapping for <strong>{domain.toUpperCase()}</strong>.
                                </p>
                                <MappingTable mapping={mapping} />

                                {step === 3 && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={() => doNormalize()} disabled={normSt.loading}>
                                            {normSt.loading
                                                ? <><span className="spinner" /> Normalizing…</>
                                                : autoProceed
                                                    ? <><Zap size={15} /> Confirm Mapping &amp; Auto Process</>
                                                    : <><ArrowRight size={15} /> Confirm Mapping &amp; Normalize</>}
                                        </button>
                                    </div>
                                )}

                                {normSt.error && (
                                    <div style={{ marginTop: '1rem' }}>
                                        <ErrorCard msg={normSt.error} onRetry={() => doNormalize()} />
                                    </div>
                                )}

                                {step > 3 && (
                                    <div className="info-card" style={{ marginTop: '1rem' }}>
                                        <CheckCircle size={16} /> Mapping confirmed.
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                )}

                {/* ════════════════════════════════════════════
                    STEP 4 — NORMALIZE
                ════════════════════════════════════════════ */}
                {step >= 4 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Wrench size={16} /> Step 4 — Normalize ({domain.toUpperCase()})</div>

                        {normSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Normalizing data…
                            </div>
                        )}

                        {!normSt.loading && !normSt.error && (
                            <>
                                <div className="info-card">
                                    <CheckCircle size={16} /> Data normalized for {domain.toUpperCase()}. File path updated for validation and ingestion.
                                </div>

                                {step === 4 && !valSt.loading && !validateResult && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={() => doValidate()}>
                                            {autoProceed ? <Zap size={15} /> : <ShieldCheck size={15} />} Run Validation
                                        </button>
                                    </div>
                                )}

                                {valSt.loading && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280', marginTop: '1rem' }}>
                                        <span className="spinner dark" /> Validating…
                                    </div>
                                )}

                                {valSt.error && (
                                    <div style={{ marginTop: '1rem' }}>
                                        <ErrorCard msg={valSt.error} onRetry={() => doValidate()} />
                                    </div>
                                )}

                                {validateResult && step === 4 && (
                                    <div style={{ marginTop: '1.25rem' }}>
                                        <div className={`verdict-badge ${validateResult.verdict === 'usable_with_warnings' ? 'warn' : 'fail'
                                            }`}>
                                            {validateResult.verdict === 'usable_with_warnings'
                                                ? <><AlertTriangle size={16} /> Data is usable — with warnings</>
                                                : <><AlertCircle size={16} /> Data cannot be ingested</>}
                                        </div>

                                        {validateResult.problems.length > 0 && (
                                            <ul className="problems-list">
                                                {validateResult.problems.map((p, i) => (
                                                    <li key={i}>
                                                        <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                                                        <span style={{ marginLeft: '0.5rem' }}>{typeof p === 'string' ? p : JSON.stringify(p)}</span>
                                                    </li>
                                                ))}
                                            </ul>
                                        )}

                                        {validateResult.verdict === 'not_usable' && (
                                            <div className="error-card">
                                                <AlertCircle size={16} /> Please fix your data and upload again.
                                            </div>
                                        )}

                                        {validateResult.verdict === 'usable_with_warnings' && (
                                            <div className="btn-actions">
                                                <button className="btn-warn" onClick={() => doIngest()}>
                                                    <ArrowRight size={15} /> Ingest Anyway
                                                </button>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                )}

                {/* ════════════════════════════════════════════
                    STEP 5 — VALIDATE SUCCESS + INGEST
                ════════════════════════════════════════════ */}
                {step >= 5 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Database size={16} /> Step 5/6 — Ingest to Knowledge Base</div>

                        {validateResult && validateResult.verdict === 'usable' && step === 5 && !ingestSt.loading && !ingestMsg && (
                            <>
                                <div className="verdict-badge ok">
                                    <CheckCircle size={16} /> Data validated — ready to ingest into {domain.toUpperCase()} Knowledge Base
                                </div>
                                <div className="btn-actions">
                                    <button className="btn-primary" onClick={() => doIngest()}>
                                        <Database size={15} /> Ingest into Knowledge Base
                                    </button>
                                </div>
                            </>
                        )}

                        {ingestSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Ingesting data into the {domain.toUpperCase()} knowledge base…
                            </div>
                        )}

                        {ingestSt.error && <ErrorCard msg={ingestSt.error} onRetry={() => doIngest()} />}

                        {step >= 6 && !ingestSt.loading && !ingestSt.error && (
                            <div className="success-card">
                                <div className="success-card-icon"><CheckCircle size={26} /></div>
                                <h3>Data Successfully Ingested!</h3>
                                <p>{(typeof ingestMsg === 'string' && ingestMsg && !ingestMsg.startsWith('{')) ? ingestMsg : `Your ${domain.toUpperCase()} data is ready. The RAG chatbot is now powered by this dataset.`}</p>
                                {kbStats && (
                                    <p style={{ marginBottom: '1.25rem', fontWeight: 600 }}>
                                        Knowledge Base: {kbStats.total_chunks.toLocaleString()} chunks · {kbStats.collection_name}
                                    </p>
                                )}
                                <button className="btn-primary" onClick={() => navigate('/chat')}>
                                    Start Chatting <ArrowRight size={15} />
                                </button>
                            </div>
                        )}
                    </div>
                )}

                {/* ── KB status — always visible ──────────────── */}
                <KBStatus
                    totalChunks={kbStats?.total_chunks ?? null}
                    collectionName={kbStats?.collection_name ?? null}
                />
            </div>
        </ErrorBoundary>
    );
}
