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
    Zap, Play, Folder, HardDrive, FileText, XCircle, RotateCcw,
    CheckSquare, Square, Search, Sparkles
} from 'lucide-react';
import { StepIndicator } from '../components/connect/StepIndicator';
import { UploadZone } from '../components/connect/UploadZone';
import { PreviewTable } from '../components/connect/PreviewTable';
import { MappingTable } from '../components/connect/MappingTable';
import { KBStatus } from '../components/connect/KBStatus';
import { useConnectSession, idleStep as idle } from '../context/FileContext';
import { useUser } from '../context/UserContext';
import '../Connect.css';

interface DiscoveredTable {
    table_name: string;
    row_count: number;
    columns: string[];
    sample_rows: Record<string, any>[];
    mapping_proposal?: any;
    error?: string;
}

interface DatabaseDiscoveryResult {
    database_name: string;
    db_type: string;
    total_tables: number;
    tables: DiscoveredTable[];
}

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
    const {
        step, setStep,
        file, setFile,
        fileName, setFileName,
        filePath, setFilePath,
        sourceType, setSourceType,
        autoProceed, setAutoProceed,
        sheetName, setSheetName,
        sheets, setSheets,
        mapping, setMapping,
        previewData, setPreviewData,
        validateResult, setValidateResult,
        ingestMsg, setIngestMsg,
        kbStats, setKbStats,
        uploadSt, setUploadSt,
        previewSt, setPreviewSt,
        mappingSt, setMappingSt,
        normSt, setNormSt,
        valSt, setValSt,
        ingestSt, setIngestSt,
        resetConnectSession,
        setActivePath
    } = useConnectSession();

    const { user, activeDomainMeta, openSettings } = useUser();
    const domain = user.domain;

    // ── SQL Connection State (local to SQL panel) ──────────────────
    const [dbType, setDbType] = useState('sqlite');
    const [connString, setConnString] = useState('');
    const [sqlQuery, setSqlQuery] = useState('');
    const [sqlMode, setSqlMode] = useState<'all_tables' | 'custom_query'>('all_tables');
    const [discoveredDb, setDiscoveredDb] = useState<DatabaseDiscoveryResult | null>(null);
    const [selectedTables, setSelectedTables] = useState<string[]>([]);
    const [isDiscovering, setIsDiscovering] = useState(false);
    const [isIngestingDb, setIsIngestingDb] = useState(false);
    const [dbIngestProgress, setDbIngestProgress] = useState<{ current: number; total: number; currentTable: string } | null>(null);
    const [dbIngestSummary, setDbIngestSummary] = useState<{
        successful_tables: number;
        total_tables: number;
        table_results: Array<{ table_name: string; status: string; rows: number; chunks: number; error?: string }>;
    } | null>(null);

    // ── Folder Watcher State (local to Watcher panel) ──────────────
    const [watchDir, setWatchDir] = useState('');
    const [pendingFiles, setPendingFiles] = useState<string[]>([]);

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
        resetConnectSession();
        if (f) {
            setFile(f);
            setFileName(f.name);
        }
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
    //  STEP 1B — AUTO-DISCOVER SQL DATABASE (1-CLICK BATCH)
    // ==============================================================
    const doDiscoverDatabase = async () => {
        if (!connString) return;
        setIsDiscovering(true);
        setUploadSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/sql/discover', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_string: connString,
                    db_type: dbType,
                    domain: domain,
                    sample_n: 5
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data: DatabaseDiscoveryResult = await res.json();
            setDiscoveredDb(data);
            const valid = data.tables.filter(t => !t.error).map(t => t.table_name);
            setSelectedTables(valid);
            setUploadSt(idle());
        } catch (e: any) {
            setUploadSt({ loading: false, error: String(e.message || 'Database discovery failed.') });
        } finally {
            setIsDiscovering(false);
        }
    };

    const doIngestEntireDatabase = async () => {
        if (!connString || selectedTables.length === 0) return;
        setIsIngestingDb(true);
        setUploadSt({ loading: true, error: null });
        setDbIngestProgress({ current: 0, total: selectedTables.length, currentTable: selectedTables[0] });

        try {
            const res = await fetch('/api/kb/ingest-database', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_string: connString,
                    db_type: dbType,
                    domain: domain,
                    tables: selectedTables,
                    strategy: 'merge'
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            setIngestMsg(data.message || `Successfully ingested database '${data.database_name}'`);
            setDbIngestSummary({
                successful_tables: data.successful_tables ?? 0,
                total_tables: data.total_tables ?? selectedTables.length,
                table_results: data.table_results ?? []
            });
            setStep(6);
            setUploadSt(idle());
            void fetchKBStats();
        } catch (e: any) {
            setUploadSt({ loading: false, error: String(e.message || 'Database ingestion failed.') });
        } finally {
            setIsIngestingDb(false);
            setDbIngestProgress(null);
        }
    };

    // ==============================================================
    //  STEP 1C — CONNECT SINGLE SQL TABLE / QUERY
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
    const [strategy, setStrategy] = useState<'merge' | 'row'>('merge');

    const doIngest = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null, targetStrategy?: 'merge' | 'row') => {
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;
        const currentStrategy = targetStrategy || strategy;

        setIngestSt({ loading: true, error: null });
        setStep(5);
        try {
            const res = await fetch('/api/kb/ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: currentFp,
                    domain: domain,
                    strategy: currentStrategy,
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

                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
                        {step > 0 && (
                            <>
                                <button
                                    type="button"
                                    className="btn-danger-outline"
                                    onClick={() => resetConnectSession('sql')}
                                    style={{
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        gap: '0.4rem',
                                        fontSize: '0.82rem',
                                        padding: '0.45rem 0.85rem',
                                        borderRadius: '8px'
                                    }}
                                    title="Cancel current progress and connect another database"
                                >
                                    <HardDrive size={13} /> Cancel &amp; Add DB
                                </button>
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => resetConnectSession('file')}
                                    style={{
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        gap: '0.4rem',
                                        fontSize: '0.82rem',
                                        padding: '0.45rem 0.85rem',
                                        borderRadius: '8px'
                                    }}
                                    title="Cancel current progress and upload a new file"
                                >
                                    <Upload size={13} /> Upload File
                                </button>
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    onClick={() => resetConnectSession()}
                                    style={{
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        gap: '0.4rem',
                                        fontSize: '0.82rem',
                                        padding: '0.45rem 0.85rem',
                                        borderRadius: '8px',
                                        border: '1px solid rgba(239, 68, 68, 0.3)',
                                        color: '#ef4444',
                                        background: 'rgba(239, 68, 68, 0.06)'
                                    }}
                                    title="Discard current wizard session"
                                >
                                    <XCircle size={13} /> Cancel Session
                                </button>
                            </>
                        )}

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
                            onClick={() => {
                                if (step > 0 && sourceType !== 'file') resetConnectSession('file');
                                else setSourceType('file');
                            }}
                        >
                            <FileText size={14} /> File Upload (CSV / Excel / JSON)
                        </button>
                        <button
                            type="button"
                            className={`source-tab-btn ${sourceType === 'sql' ? 'active' : ''}`}
                            onClick={() => {
                                if (step > 0 && sourceType !== 'sql') resetConnectSession('sql');
                                else setSourceType('sql');
                            }}
                        >
                            <HardDrive size={14} /> SQL Database Connection
                        </button>
                        <button
                            type="button"
                            className={`source-tab-btn ${sourceType === 'watcher' ? 'active' : ''}`}
                            onClick={() => {
                                if (step > 0 && sourceType !== 'watcher') resetConnectSession('watcher');
                                else setSourceType('watcher');
                            }}
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
                            {/* Sub-mode selector: Auto-Discover All Tables vs Single Query */}
                            <div className="db-mode-selector">
                                <button
                                    type="button"
                                    className={`db-mode-tab ${sqlMode === 'all_tables' ? 'active' : ''}`}
                                    onClick={() => setSqlMode('all_tables')}
                                >
                                    <Sparkles size={15} /> ⚡ 1-Click Auto-Discover &amp; Ingest All Tables (POS / DB)
                                </button>
                                <button
                                    type="button"
                                    className={`db-mode-tab ${sqlMode === 'custom_query' ? 'active' : ''}`}
                                    onClick={() => setSqlMode('custom_query')}
                                >
                                    <HardDrive size={15} /> ⚙️ Single Table / Custom Query
                                </button>
                            </div>

                            <div className="sql-form-group">
                                <label>Database Engine:</label>
                                <select
                                    className="sql-form-input"
                                    value={dbType}
                                    onChange={e => setDbType(e.target.value)}
                                >
                                    <option value="sqlite">SQLite (.db / .sqlite)</option>
                                    <option value="mssql">MS SQL Server (SQL Express / POS)</option>
                                    <option value="postgresql">PostgreSQL</option>
                                    <option value="mysql">MySQL / MariaDB</option>
                                </select>
                            </div>

                            <div className="sql-form-group">
                                <label>Connection String or File Path:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder={
                                        dbType === 'sqlite'
                                            ? 'C:/POS_Software/pharmacy.db'
                                            : dbType === 'mssql'
                                            ? 'mssql+pyodbc://sa:Password123@localhost/PharmacyPOS?driver=ODBC+Driver+17+for+SQL+Server'
                                            : dbType === 'postgresql'
                                            ? 'postgresql://postgres:password@localhost:5432/pharmacy_pos'
                                            : 'mysql+pymysql://root:password@localhost:3306/pharmacy_db'
                                    }
                                    value={connString}
                                    onChange={e => {
                                        const val = e.target.value;
                                        setConnString(val);
                                        const lower = val.trim().toLowerCase();
                                        if (lower.startsWith('mssql') || lower.includes('sqlexpress') || lower.includes('driver=')) {
                                            setDbType('mssql');
                                        } else if (lower.startsWith('postgres')) {
                                            setDbType('postgresql');
                                        } else if (lower.startsWith('mysql') || lower.startsWith('mariadb')) {
                                            setDbType('mysql');
                                        } else if (lower.startsWith('sqlite') || lower.endsWith('.db') || lower.endsWith('.sqlite')) {
                                            setDbType('sqlite');
                                        }
                                    }}
                                />
                            </div>

                            {/* ── MODE A: 1-CLICK DISCOVERY & INGEST ALL TABLES ── */}
                            {sqlMode === 'all_tables' && (
                                <>
                                    {step === 0 && !discoveredDb && (
                                        <div className="btn-actions" style={{ marginTop: '1rem' }}>
                                            <button
                                                className="btn-primary"
                                                onClick={doDiscoverDatabase}
                                                disabled={!connString || isDiscovering || uploadSt.loading}
                                            >
                                                {isDiscovering
                                                    ? <><span className="spinner" /> Scanning Database Schema…</>
                                                    : <><Search size={15} /> 🔍 Scan &amp; Discover All Tables</>}
                                            </button>
                                        </div>
                                    )}

                                    {discoveredDb && (
                                        <div className="db-discovery-card">
                                            <div className="db-discovery-header">
                                                <div className="db-title-group">
                                                    <Database size={20} color="var(--accent-teal, #0D7377)" />
                                                    <div>
                                                        <div style={{ fontWeight: 700, fontSize: '0.95rem', color: '#0F172A' }}>
                                                            Database: <span style={{ color: 'var(--accent-teal, #0D7377)' }}>{discoveredDb.database_name}</span>
                                                        </div>
                                                        <div style={{ fontSize: '0.78rem', color: '#64748B' }}>
                                                            Engine: {discoveredDb.db_type.toUpperCase()} · {discoveredDb.tables.length} tables found
                                                        </div>
                                                    </div>
                                                </div>
                                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                    <button
                                                        type="button"
                                                        className="btn-secondary"
                                                        style={{ padding: '0.35rem 0.75rem', fontSize: '0.78rem' }}
                                                        onClick={() => {
                                                            const all = discoveredDb.tables.filter(t => !t.error).map(t => t.table_name);
                                                            if (selectedTables.length === all.length) {
                                                                setSelectedTables([]);
                                                            } else {
                                                                setSelectedTables(all);
                                                            }
                                                        }}
                                                    >
                                                        {selectedTables.length === discoveredDb.tables.filter(t => !t.error).length
                                                            ? <><Square size={13} /> Deselect All</>
                                                            : <><CheckSquare size={13} /> Select All ({discoveredDb.tables.length})</>}
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="btn-secondary"
                                                        style={{ padding: '0.35rem 0.65rem', fontSize: '0.78rem' }}
                                                        onClick={doDiscoverDatabase}
                                                        title="Re-scan database"
                                                    >
                                                        <RefreshCw size={13} />
                                                    </button>
                                                </div>
                                            </div>

                                            <div className="db-tables-grid">
                                                {discoveredDb.tables.map(t => {
                                                    const isChecked = selectedTables.includes(t.table_name);
                                                    return (
                                                        <div
                                                            key={t.table_name}
                                                            className={`db-table-item ${isChecked ? 'selected' : ''}`}
                                                            onClick={() => {
                                                                if (t.error) return;
                                                                setSelectedTables(prev =>
                                                                    prev.includes(t.table_name)
                                                                        ? prev.filter(x => x !== t.table_name)
                                                                        : [...prev, t.table_name]
                                                                );
                                                            }}
                                                        >
                                                            <input
                                                                type="checkbox"
                                                                className="db-table-checkbox"
                                                                checked={isChecked}
                                                                disabled={!!t.error}
                                                                onChange={() => {}} /* Handled by parent div onClick */
                                                            />
                                                            <div className="db-table-info">
                                                                <div className="db-table-name">📊 {t.table_name}</div>
                                                                <div className="db-table-meta">
                                                                    {t.error ? (
                                                                        <span style={{ color: '#EF4444' }}>⚠️ {t.error}</span>
                                                                    ) : (
                                                                        <>
                                                                            <span>{t.row_count.toLocaleString()} rows</span>
                                                                            <span>·</span>
                                                                            <span>{t.columns.length} cols</span>
                                                                        </>
                                                                    )}
                                                                </div>
                                                            </div>
                                                        </div>
                                                    );
                                                })}
                                            </div>

                                            {isIngestingDb && (
                                                <div className="batch-progress-box">
                                                    <span className="spinner" />
                                                    <span>
                                                        {dbIngestProgress
                                                            ? `Ingesting table ${dbIngestProgress.current + 1} of ${dbIngestProgress.total}: '${dbIngestProgress.currentTable}' into KnowledgeBase…`
                                                            : `Ingesting tables into KnowledgeBase… (${selectedTables.length} tables selected)`}
                                                    </span>
                                                </div>
                                            )}

                                            <div className="btn-actions" style={{ marginTop: '1rem' }}>
                                                <button
                                                    className="btn-primary"
                                                    onClick={doIngestEntireDatabase}
                                                    disabled={selectedTables.length === 0 || isIngestingDb}
                                                >
                                                    {isIngestingDb ? (
                                                        <><span className="spinner" /> Ingesting Entire Database…</>
                                                    ) : (
                                                        <><Zap size={16} /> ⚡ Ingest Entire Database ({selectedTables.length} Tables)</>
                                                    )}
                                                </button>
                                            </div>
                                        </div>
                                    )}
                                </>
                            )}

                            {/* ── MODE B: CUSTOM TABLE / SINGLE QUERY ── */}
                            {sqlMode === 'custom_query' && (
                                <>
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
                                </>
                            )}

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={() => sqlMode === 'all_tables' ? doDiscoverDatabase() : doConnectSQL()} />
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

                    {/* Success confirmation and Disconnect / Switch options */}
                    {step >= 1 && (
                        <div className="info-card" style={{ marginTop: '1rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.75rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                <CheckCircle size={16} />
                                <span>
                                    Source connected for domain: <strong>{domain.toUpperCase()}</strong>
                                    {sourceType === 'sql' ? ` (${dbType.toUpperCase()})` : sourceType === 'file' ? ` (${fileName || 'File'})` : ''}.
                                </span>
                            </div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                                <button
                                    type="button"
                                    className="btn-danger-outline"
                                    style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                                    onClick={() => resetConnectSession('sql')}
                                    title="Cancel current progress and connect a different SQL database"
                                >
                                    <RotateCcw size={13} /> Cancel &amp; Add Another DB
                                </button>
                                <button
                                    type="button"
                                    className="btn-secondary"
                                    style={{ padding: '0.35rem 0.75rem', fontSize: '0.8rem' }}
                                    onClick={() => resetConnectSession('file')}
                                    title="Switch to file upload"
                                >
                                    <Upload size={13} /> Switch to File
                                </button>
                            </div>
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
                                <button
                                    type="button"
                                    className="btn-danger-outline"
                                    onClick={() => resetConnectSession('sql')}
                                    title="Cancel preview and connect another SQL database"
                                >
                                    <XCircle size={15} /> Cancel &amp; Add Another DB
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
                                    <button
                                        type="button"
                                        className="btn-danger-outline"
                                        onClick={() => resetConnectSession('sql')}
                                        title="Cancel preview and connect another SQL database"
                                    >
                                        <XCircle size={15} /> Cancel &amp; Add Another DB
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => resetConnectSession('file')}
                                        title="Cancel preview and upload a file instead"
                                    >
                                        <Upload size={15} /> Upload File Instead
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
                                        <button
                                            type="button"
                                            className="btn-danger-outline"
                                            onClick={() => resetConnectSession('sql')}
                                            title="Cancel mapping and connect another SQL database"
                                        >
                                            <XCircle size={15} /> Cancel &amp; Add Another DB
                                        </button>
                                        <button
                                            type="button"
                                            className="btn-secondary"
                                            onClick={() => resetConnectSession('file')}
                                            title="Cancel and switch to file upload"
                                        >
                                            <Upload size={15} /> Upload File Instead
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
                                        <button
                                            type="button"
                                            className="btn-danger-outline"
                                            onClick={() => resetConnectSession('sql')}
                                            title="Cancel normalization and connect another SQL database"
                                        >
                                            <XCircle size={15} /> Cancel &amp; Add Another DB
                                        </button>
                                        <button
                                            type="button"
                                            className="btn-secondary"
                                            onClick={() => resetConnectSession('file')}
                                        >
                                            <Upload size={15} /> Upload File Instead
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
                                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                                <div className="error-card">
                                                    <AlertCircle size={16} /> Please fix your data or connect a different database source.
                                                </div>
                                                <div className="btn-actions">
                                                    <button
                                                        type="button"
                                                        className="btn-danger-outline"
                                                        onClick={() => resetConnectSession('sql')}
                                                    >
                                                        <RotateCcw size={15} /> Cancel &amp; Connect Another DB
                                                    </button>
                                                    <button
                                                        type="button"
                                                        className="btn-secondary"
                                                        onClick={() => resetConnectSession('file')}
                                                    >
                                                        <Upload size={15} /> Upload File Instead
                                                    </button>
                                                </div>
                                            </div>
                                        )}

                                        {validateResult.verdict === 'usable_with_warnings' && (
                                            <div className="btn-actions">
                                                <button className="btn-warn" onClick={() => doIngest()}>
                                                    <ArrowRight size={15} /> Ingest Anyway
                                                </button>
                                                <button
                                                    type="button"
                                                    className="btn-danger-outline"
                                                    onClick={() => resetConnectSession('sql')}
                                                >
                                                    <XCircle size={15} /> Cancel &amp; Add Another DB
                                                </button>
                                                <button
                                                    type="button"
                                                    className="btn-secondary"
                                                    onClick={() => resetConnectSession('file')}
                                                >
                                                    <Upload size={15} /> Upload File Instead
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

                        {validateResult && (validateResult.verdict === 'usable' || validateResult.verdict === 'usable_with_warnings') && step === 5 && !ingestSt.loading && !ingestMsg && (
                            <>
                                {validateResult.verdict === 'usable' ? (
                                    <div className="verdict-badge ok">
                                        <CheckCircle size={16} /> Data validated — ready to ingest into {domain.toUpperCase()} Knowledge Base
                                    </div>
                                ) : (
                                    <>
                                        <div className="verdict-badge warn">
                                            <AlertTriangle size={16} /> Data validated with minor warnings — ready to ingest into {domain.toUpperCase()} Knowledge Base
                                        </div>
                                        {validateResult.problems && validateResult.problems.length > 0 && (
                                            <ul className="problems-list" style={{ marginTop: '0.75rem', marginBottom: '0.75rem', maxHeight: '130px', overflowY: 'auto' }}>
                                                {validateResult.problems.slice(0, 5).map((p: any, i: number) => (
                                                    <li key={i}>
                                                        <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                                                        <span style={{ marginLeft: '0.5rem' }}>{typeof p === 'string' ? p : p.message || p.description || JSON.stringify(p)}</span>
                                                    </li>
                                                ))}
                                                {validateResult.problems.length > 5 && (
                                                    <li style={{ color: '#64748b', fontStyle: 'italic', fontSize: '0.75rem' }}>
                                                        +{validateResult.problems.length - 5} more non-critical notice(s)
                                                    </li>
                                                )}
                                            </ul>
                                        )}
                                    </>
                                )}

                                <div style={{ marginTop: '1.25rem', marginBottom: '1.25rem', background: '#F8FAFC', padding: '1rem', borderRadius: '10px', border: '1px solid #E2E8F0' }}>
                                    <label style={{ fontSize: '0.85rem', fontWeight: 600, color: '#334155', display: 'block', marginBottom: '0.6rem' }}>
                                        ⚡ Ingestion &amp; Chunking Strategy:
                                    </label>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                                        <div
                                            onClick={() => setStrategy('merge')}
                                            style={{
                                                padding: '0.75rem 1rem',
                                                borderRadius: '8px',
                                                border: strategy === 'merge' ? '2px solid #2563EB' : '1px solid #CBD5E1',
                                                background: strategy === 'merge' ? '#EFF6FF' : '#FFFFFF',
                                                cursor: 'pointer',
                                                transition: 'all 0.15s ease'
                                            }}
                                        >
                                            <div style={{ fontWeight: 600, fontSize: '0.88rem', color: strategy === 'merge' ? '#1D4ED8' : '#1E293B', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                                <span>⚡ Merge / Grouping</span>
                                                <span style={{ fontSize: '0.7rem', background: '#10B981', color: '#FFF', padding: '2px 6px', borderRadius: '10px', fontWeight: 700 }}>5x–10x FASTER</span>
                                            </div>
                                            <div style={{ fontSize: '0.75rem', color: '#64748B', marginTop: '0.3rem', lineHeight: '1.3' }}>
                                                Groups rows by invoice/bill number into rich transaction chunks. Superior context &amp; fastest vectorization.
                                            </div>
                                        </div>

                                        <div
                                            onClick={() => setStrategy('row')}
                                            style={{
                                                padding: '0.75rem 1rem',
                                                borderRadius: '8px',
                                                border: strategy === 'row' ? '2px solid #2563EB' : '1px solid #CBD5E1',
                                                background: strategy === 'row' ? '#EFF6FF' : '#FFFFFF',
                                                cursor: 'pointer',
                                                transition: 'all 0.15s ease'
                                            }}
                                        >
                                            <div style={{ fontWeight: 600, fontSize: '0.88rem', color: strategy === 'row' ? '#1D4ED8' : '#1E293B' }}>
                                                📄 Row-by-Row
                                            </div>
                                            <div style={{ fontSize: '0.75rem', color: '#64748B', marginTop: '0.3rem', lineHeight: '1.3' }}>
                                                Embeds each row individually. Useful for independent catalog or item inventories.
                                            </div>
                                        </div>
                                    </div>
                                </div>

                                <div className="btn-actions">
                                    <button className="btn-primary" onClick={() => doIngest()}>
                                        <Database size={15} /> {validateResult.verdict === 'usable_with_warnings' ? 'Ingest into Knowledge Base (Proceed with Warnings)' : 'Ingest into Knowledge Base'}
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-danger-outline"
                                        onClick={() => resetConnectSession('sql')}
                                        title="Cancel ingestion and connect another SQL database"
                                    >
                                        <XCircle size={15} /> Cancel &amp; Add Another DB
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => resetConnectSession('file')}
                                    >
                                        <Upload size={15} /> Upload File Instead
                                    </button>
                                </div>
                            </>
                        )}

                        {step === 5 && !validateResult && !valSt.loading && !ingestSt.loading && !ingestMsg && (
                            <div style={{ padding: '0.75rem 0' }}>
                                <div className="info-card" style={{ marginBottom: '1rem' }}>
                                    <ShieldCheck size={16} /> Ready for quality validation before ingesting into {domain.toUpperCase()} Knowledge Base.
                                </div>
                                <div className="btn-actions">
                                    <button className="btn-primary" onClick={() => doValidate()}>
                                        <ShieldCheck size={15} /> Run Data Validation
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => resetConnectSession('file')}
                                    >
                                        <Upload size={15} /> Choose Another File
                                    </button>
                                </div>
                            </div>
                        )}

                        {ingestSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Ingesting data into the {domain.toUpperCase()} knowledge base…
                            </div>
                        )}

                        {ingestSt.error && <ErrorCard msg={ingestSt.error} onRetry={() => doIngest()} />}

                        {step >= 6 && !ingestSt.loading && !ingestSt.error && (
                            <div className="success-card">
                                <div className="success-card-icon">
                                    {dbIngestSummary && dbIngestSummary.successful_tables === 0 ? (
                                        <AlertTriangle size={26} color="#D97706" />
                                    ) : (
                                        <CheckCircle size={26} />
                                    )}
                                </div>
                                <h3>
                                    {dbIngestSummary && dbIngestSummary.successful_tables === 0
                                        ? 'Database Ingestion Notice'
                                        : 'Data Successfully Ingested!'}
                                </h3>
                                <p>{(typeof ingestMsg === 'string' && ingestMsg && !ingestMsg.startsWith('{')) ? ingestMsg : `Your ${domain.toUpperCase()} data is ready. The RAG chatbot is now powered by this dataset.`}</p>

                                {dbIngestSummary && dbIngestSummary.table_results && dbIngestSummary.table_results.length > 0 && (
                                    <div style={{ margin: '1rem 0 1.25rem 0', textAlign: 'left', background: '#F8FAFC', borderRadius: '8px', border: '1px solid #E2E8F0', padding: '0.75rem', maxHeight: '200px', overflowY: 'auto' }}>
                                        <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#475569', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                                            Table Ingestion Breakdown:
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                            {dbIngestSummary.table_results.map((t, idx) => (
                                                <div key={idx} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.82rem', padding: '4px 8px', borderRadius: '4px', background: '#FFFFFF', border: '1px solid #EDF2F7' }}>
                                                    <span style={{ fontWeight: 600, color: '#1E293B' }}>📊 {t.table_name}</span>
                                                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                        <span style={{ color: '#64748B', fontSize: '0.75rem' }}>{t.rows} rows · {t.chunks} chunks</span>
                                                        <span style={{
                                                            fontSize: '0.72rem',
                                                            fontWeight: 600,
                                                            padding: '2px 7px',
                                                            borderRadius: '999px',
                                                            background: t.status === 'success' ? '#DEF7EC' : t.status === 'empty' ? '#F1F5F9' : '#FDE8E8',
                                                            color: t.status === 'success' ? '#03543F' : t.status === 'empty' ? '#64748B' : '#9B1C1C'
                                                        }}>
                                                            {t.status === 'success' ? 'Ingested' : t.status === 'empty' ? 'Empty (0 rows)' : 'Failed'}
                                                        </span>
                                                    </span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {kbStats && (
                                    <p style={{ marginBottom: '1.25rem', fontWeight: 600 }}>
                                        Knowledge Base: {kbStats.total_chunks.toLocaleString()} chunks · {kbStats.collection_name}
                                    </p>
                                )}
                                <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'center', flexWrap: 'wrap' }}>
                                    <button className="btn-primary" onClick={() => navigate('/chat')}>
                                        Start Chatting <ArrowRight size={15} />
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => resetConnectSession('sql')}
                                        style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                                    >
                                        <HardDrive size={15} /> Connect Another Database
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={() => resetConnectSession('file')}
                                        style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}
                                    >
                                        <Upload size={15} /> Upload Another File
                                    </button>
                                </div>
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
