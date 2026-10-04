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
    Zap, Play, Folder, HardDrive, FileText, XCircle, RotateCcw, ShoppingBag,
    CheckSquare, Square, Search, Sparkles
} from 'lucide-react';
import { StepIndicator } from '../components/connect/StepIndicator';
import { UploadZone } from '../components/connect/UploadZone';
import { PreviewTable } from '../components/connect/PreviewTable';
import { MappingTable } from '../components/connect/MappingTable';
import { KBStatus } from '../components/connect/KBStatus';
import { useConnectSession, idleStep as idle, markDataChanged } from '../context/FileContext';
import { useUser } from '../context/UserContext';
import '../Connect.css';

interface DiscoveredTable {
    table_name: string;
    row_count: number;
    columns: string[];
    sample_rows: Record<string, any>[];
    mapping_proposal?: any;
    canonical_fields?: string[];
    saved_profile?: { mapping: Record<string, string> } | null;
    error?: string;
}

interface DatabaseDiscoveryResult {
    database_name: string;
    db_type: string;
    total_tables: number;
    tables: DiscoveredTable[];
}

interface DetectedSqlDb {
    database_name: string;
    file_name: string;
    file_path: string;
    server: string;
    is_attached: boolean;
    table_count: number;
    tables: string[];
    connection_string: string;
    error?: string;
}

interface LocalSqlInstance {
    database_name: string;
    server: string;
    driver: string;
    connection_string: string;
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
        return { hasError: true, message: err?.message || 'An unexpected rendering error occurred' };
    }
    render() {
        if (this.state.hasError) {
            return (
                <div className="error-card" style={{ margin: '2rem' }}>
                    <AlertCircle size={20} />
                    <div>
                        <strong>Something went wrong in the Connect Source wizard.</strong>
                        <div style={{ marginTop: '0.25rem', fontFamily: 'monospace', fontSize: '0.82rem' }}>
                            {this.state.message}
                        </div>
                        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
                            <button
                                className="btn-secondary"
                                onClick={() => this.setState({ hasError: false, message: '' })}
                            >
                                Try Again
                            </button>
                            <button
                                className="btn-danger-outline"
                                onClick={() => {
                                    try { sessionStorage.removeItem('llm_konnect_connect_session'); } catch {}
                                    window.location.reload();
                                }}
                            >
                                Reset Wizard &amp; Session
                            </button>
                        </div>
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
//  ConnectSourceContent — main wizard component
// ============================================================
function ConnectSourceContent() {
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
        ingestProgress, setIngestProgress,
        resetConnectSession,
        setActivePath
    } = useConnectSession();

    const { user } = useUser();
    const domain = user.domain;
    const [canonicalFields, setCanonicalFields] = useState<string[]>([]);
    useEffect(() => {
        let active = true;
        setCanonicalFields([]);
        fetch(`/api/sources/schema?domain=${encodeURIComponent(domain)}`)
            .then(response => response.ok ? response.json() : null)
            .then(data => { if (active && data) setCanonicalFields(data.canonical_fields || []); })
            .catch(() => { /* Preview metadata remains available when this lookup fails. */ });
        return () => { active = false; };
    }, [domain]);

    // ── SQL Connection State (local to SQL panel) ──────────────────
    const [dbType, setDbType] = useState('sqlite');
    const [connString, setConnString] = useState('');
    const [sqlQuery, setSqlQuery] = useState('');
    const [sqlMode, setSqlMode] = useState<'all_tables' | 'custom_query'>('all_tables');
    const [discoveredDb, setDiscoveredDb] = useState<DatabaseDiscoveryResult | null>(null);
    const [selectedTables, setSelectedTables] = useState<string[]>([]);
    const [tableMappings, setTableMappings] = useState<Record<string, Record<string, string>>>({});
    const [isDiscovering, setIsDiscovering] = useState(false);
    const [isIngestingDb, setIsIngestingDb] = useState(false);
    const [dbIngestProgress, setDbIngestProgress] = useState<{ current: number; total: number; currentTable: string } | null>(null);
    const [dbIngestSummary, setDbIngestSummary] = useState<{
        successful_tables: number;
        total_tables: number;
        table_results: Array<{
            table_name: string;
            status: string;
            rows: number;
            chunks: number;
            new_rows?: number;
            updated_rows?: number;
            deleted_rows?: number;
            message?: string;
            error?: string;
        }>;
    } | null>(null);

    // ── Folder Watcher State (local to Watcher panel) ──────────────
    const [watchDir, setWatchDir] = useState('');
    const [pendingFiles, setPendingFiles] = useState<string[]>([]);
    const [detectedSqlDb, setDetectedSqlDb] = useState<DetectedSqlDb | null>(null);
    const [localSqlInstances, setLocalSqlInstances] = useState<LocalSqlInstance[]>([]);
    const [isScanningLocalSql, setIsScanningLocalSql] = useState(false);

    // ── Tally Live State ───────────────────────────────────────────
    const [tallyUrl, setTallyUrl] = useState('http://localhost:9000');
    const [tallyTimeout, setTallyTimeout] = useState(10);
    const [tallyTestMsg, setTallyTestMsg] = useState<{ status: string; text: string } | null>(null);
    const [isTestingTally, setIsTestingTally] = useState(false);

    // ── Shopify Live State ─────────────────────────────────────────
    const [shopifyStore, setShopifyStore] = useState('');
    const [shopifyToken, setShopifyToken] = useState('');
    const [shopifyResource, setShopifyResource] = useState('orders');
    const [shopifyTestMsg, setShopifyTestMsg] = useState<{ status: string; text: string } | null>(null);
    const [isTestingShopify, setIsTestingShopify] = useState(false);



    // ── Load KB stats and local databases on mount ────────────────
    useEffect(() => {
        document.title = 'Connect Source — LLM-KONNECT';
        void fetchKBStats();
        void fetchLocalSqlInstances();
    }, []);

    // ── Guard domain-specific source types ─────────────────────────
    useEffect(() => {
        if (domain === 'pharmacy' && sourceType === 'shopify') {
            resetConnectSession('file');
        } else if (domain === 'ecommerce' && sourceType === 'tally') {
            resetConnectSession('file');
        } else if ((domain === 'home_finance' || domain === 'finance') && sourceType === 'shopify') {
            resetConnectSession('file');
        }
    }, [domain, sourceType]);

    const fetchKBStats = async () => {
        try {
            const res = await fetch('/api/kb/stats');
            if (res.ok) setKbStats(await res.json());
        } catch { /* non-critical */ }
    };

    const fetchLocalSqlInstances = async () => {
        setIsScanningLocalSql(true);
        try {
            const res = await fetch('/api/sources/sql/local-instances');
            if (res.ok) {
                const data = await res.json();
                if (data.instances) setLocalSqlInstances(data.instances);
            }
        } catch { /* non-critical */ }
        finally {
            setIsScanningLocalSql(false);
        }
    };

    // ── Continuous sync with active background ingestion for this file ────────
    useEffect(() => {
        const activeFp = filePath || file?.name;
        // ONLY sync background ingestion progress if we are at Step 5 or ingesting.
        // Never hijack step when the user is in earlier steps (0 to 4)!
        if (!activeFp || (step < 5 && !ingestSt.loading)) return;

        let isMounted = true;
        const checkActiveIngest = async () => {
            try {
                const res = await fetch(`/api/kb/ingest-progress?file_path=${encodeURIComponent(activeFp)}`);
                if (!res.ok) return;
                const pData = await res.json();
                if (!isMounted) return;

                if (pData && pData.status === 'processing') {
                    const stepStr = pData.step_text || '';
                    const match = stepStr.match(/(\d+)\s*\/\s*(\d+)/);
                    const tot = match ? parseInt(match[2], 10) : (previewData?.total_rows || 100);
                    let pct = typeof pData.progress === 'number' && pData.progress > 0 ? pData.progress : 15;
                    let cur = match ? parseInt(match[1], 10) : Math.round((pct / 100) * tot);

                    setIngestSt({ loading: true, error: null });
                    setStep(5);
                    setIngestProgress({
                        percent: pct,
                        currentChunks: cur,
                        totalChunks: tot,
                        stepText: stepStr || `Ingesting chunks: ${cur} / ${tot} (${Math.round(pct)}%)...`
                    });
                } else if (pData && pData.status === 'active' && ingestSt.loading) {
                    setIngestSt(idle());
                    setIngestProgress(null);
                    setStep(6);
                    void fetchKBStats();
                }
            } catch {}
        };

        void checkActiveIngest();
        const interval = setInterval(checkActiveIngest, 1000);
        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, [filePath, file, step, ingestSt.loading, previewData?.total_rows]);

    // Smooth auto-scroll to the active step as wizard advances
    useEffect(() => {
        if (step > 0) {
            const timer = setTimeout(() => {
                const el = document.getElementById(`wizard-step-${step}`)
                    || document.getElementById(`wizard-step-${step - 1}`)
                    || document.getElementById(`wizard-step-${step - 2}`);
                if (el) {
                    el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
            }, 80);
            return () => clearTimeout(timer);
        }
    }, [step]);

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
            if (!res.ok) {
                const errJson = await res.json().catch(() => null);
                const errMsg = errJson?.detail || await res.text().catch(() => 'Upload failed');
                throw new Error(errMsg);
            }

            const data = await res.json();
            const fp: string = data.file_path;
            setFilePath(fp);

            let firstSheet: string | null = null;
            if (/\.(xlsx|xls|xlx|xlsm)$/i.test(file.name)) {
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

            // In both manual and auto modes, load preview data for Step 2.
            // If isAuto is false (Manual mode), doPreview stops here awaiting user confirmation.
            // If isAuto is true (Auto mode), doPreview automatically cascades through to Ingest.
            await doPreview(fp, firstSheet, isAuto);
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
            if (!res.ok) {
                const errJson = await res.json().catch(() => null);
                const errMsg = errJson?.detail || await res.text().catch(() => 'Database discovery failed.');
                throw new Error(errMsg);
            }
            const data: DatabaseDiscoveryResult = await res.json();
            setDiscoveredDb(data);
            const initial: Record<string, Record<string, string>> = {};
            data.tables.forEach(table => {
                initial[table.table_name] = table.saved_profile?.mapping || Object.fromEntries((table.mapping_proposal?.suggestions || []).filter((suggestion: any) => suggestion.canonical_field).map((suggestion: any) => [suggestion.source_column, suggestion.canonical_field]));
            });
            setTableMappings(initial);
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
                    table_mappings: tableMappings,
                    strategy: 'row'
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
            if (data.database_name) {
                setActivePath(`db://${data.database_name}`);
                try {
                    sessionStorage.removeItem('llm_konnect_user_manual_dataset_choice');
                } catch {}
            }
            setStep(6);
            markDataChanged();
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
            if (!res.ok) {
                const errJson = await res.json().catch(() => null);
                const errMsg = errJson?.detail || await res.text().catch(() => 'SQL connection failed.');
                throw new Error(errMsg);
            }
            const data = await res.json();

            setPreviewData({
                columns: data.columns || [],
                sample_rows: data.data ? data.data.map((r: any) => (data.columns || []).map((c: string) => r[c])) : [],
                total_rows: data.total_rows || (data.data ? data.data.length : 0),
                canonical_fields: data.canonical_fields || [],
                mapping_confirmed: Boolean(data.saved_profile)
            });

            const proposedMap: Record<string, string> = {};
            if (data.mapping_proposal && data.mapping_proposal.suggestions) {
                data.mapping_proposal.suggestions.forEach((s: any) => {
                    if (s.canonical_field) proposedMap[s.source_column] = s.canonical_field;
                });
            }
            const selectedMap = data.saved_profile?.mapping || proposedMap;
            setMapping(selectedMap);

            setUploadSt(idle());
            setStep(1);

            if (isAuto) {
                await delay(400);
                if (data.saved_profile) await doIngestSingleSqlTable(selectedMap);
                else setStep(2);
            }
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'SQL connection failed.') });
        }
    };

    const doIngestSingleSqlTable = async (confirmedMap?: Record<string, string>) => {
        if (!connString || !sqlQuery) return;
        let tName = sqlQuery.trim();
        const m = tName.match(/from\s+\[?([a-zA-Z0-9_#]+)\]?/i);
        if (m) tName = m[1];

        setIsIngestingDb(true);
        setUploadSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/kb/ingest-database', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_string: connString,
                    db_type: dbType,
                    domain: domain,
                    tables: [tName],
                    table_mappings: { [tName]: confirmedMap || mapping },
                    strategy: 'row'
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            setIngestMsg(data.message || `Successfully ingested table '${tName}'`);
            setDbIngestSummary({
                successful_tables: data.successful_tables ?? 1,
                total_tables: 1,
                table_results: data.table_results ?? []
            });
            setStep(6);
            markDataChanged();
            setUploadSt(idle());
            void fetchKBStats();
        } catch (e: any) {
            setUploadSt({ loading: false, error: String(e.message || 'Table ingestion failed.') });
        } finally {
            setIsIngestingDb(false);
        }
    };

    // ==============================================================
    //  STEP 1C — FOLDER WATCHER
    // ==============================================================
    const doCheckWatcher = async () => {
        if (!watchDir.trim()) {
            setUploadSt({ loading: false, error: 'Please enter a valid directory path or database file path.' });
            return;
        }
        setUploadSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/watcher/list', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ watch_dir: watchDir.trim(), domain: domain })
            });
            if (!res.ok) {
                const errJson = await res.json().catch(() => null);
                const errMsg = errJson?.detail || await res.text().catch(() => 'Directory scan failed.');
                throw new Error(errMsg);
            }
            const data = await res.json();
            const files: string[] = data.pending_files || [];
            setPendingFiles(files);
            if (data.detected_sql_db) {
                setDetectedSqlDb(data.detected_sql_db);
                if (data.detected_sql_db.file_path && data.detected_sql_db.file_path !== watchDir) {
                    setWatchDir(data.detected_sql_db.file_path);
                }
            } else {
                setDetectedSqlDb(null);
            }
            if (files.length > 0) {
                setFilePath(files[0]);
            }
            if (files.length === 0 && !data.detected_sql_db) {
                setUploadSt({
                    loading: false,
                    error: `No compatible data files (.csv, .xlsx, .json, .mdf, .stardb, .db, .sqlite) found at: "${watchDir}". Please verify the folder or file path.`
                });
            } else {
                setUploadSt(idle());
            }
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'Directory scan failed.') });
        }
    };

    const handleConnectDetectedSql = async (targetConn?: string) => {
        const connToUse = targetConn || detectedSqlDb?.file_path || detectedSqlDb?.connection_string || watchDir;
        setSourceType('sql');
        setDbType(connToUse.toLowerCase().endsWith('.stardb') || connToUse.toLowerCase().endsWith('.db') || connToUse.toLowerCase().endsWith('.sqlite') || connToUse.toLowerCase().endsWith('.sqlite3') || (detectedSqlDb?.server || '').toLowerCase().includes('sqlite') ? 'sqlite' : 'mssql');
        setConnString(connToUse);
        setIsDiscovering(true);
        setUploadSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/sql/discover', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    connection_string: connToUse,
                    db_type: (connToUse.toLowerCase().endsWith('.stardb') || connToUse.toLowerCase().endsWith('.db') || connToUse.toLowerCase().endsWith('.sqlite') || connToUse.toLowerCase().endsWith('.sqlite3') || (detectedSqlDb?.server || '').toLowerCase().includes('sqlite')) ? 'sqlite' : 'mssql',
                    domain: domain,
                    sample_n: 5
                })
            });
            if (!res.ok) {
                const errJson = await res.json().catch(() => null);
                const errMsg = errJson?.detail || await res.text().catch(() => 'Database discovery failed.');
                throw new Error(errMsg);
            }
            const data: DatabaseDiscoveryResult = await res.json();
            setDiscoveredDb(data);
            const initial: Record<string, Record<string, string>> = {};
            data.tables.forEach(table => {
                initial[table.table_name] = table.saved_profile?.mapping || Object.fromEntries((table.mapping_proposal?.suggestions || []).filter((suggestion: any) => suggestion.canonical_field).map((suggestion: any) => [suggestion.source_column, suggestion.canonical_field]));
            });
            setTableMappings(initial);
            const valid = data.tables.filter(t => !t.error).map(t => t.table_name);
            setSelectedTables(valid);
            setUploadSt(idle());
        } catch (e: any) {
            setUploadSt({ loading: false, error: String(e.message || 'Database discovery failed.') });
        } finally {
            setIsDiscovering(false);
        }
    };

        // ==============================================================
    //  TALLY & SHOPIFY CONNECT HANDLERS
    // ==============================================================
    const doTestTally = async () => {
        setIsTestingTally(true);
        setTallyTestMsg(null);
        try {
            const res = await fetch('/api/sources/tally/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: tallyUrl.trim(), timeout: tallyTimeout })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Connection failed');
            setTallyTestMsg({ status: 'success', text: `Connected successfully! Found ${data.total_preview_rows} sample voucher records.` });
        } catch (err: any) {
            setTallyTestMsg({ status: 'error', text: err.message || 'Could not connect to Tally.' });
        } finally {
            setIsTestingTally(false);
        }
    };

    const doConnectTally = async (overrideAuto?: boolean) => {
        const isAuto = overrideAuto ?? autoProceed;
        const targetUrl = tallyUrl.trim() || 'http://localhost:9000';
        setFilePath(targetUrl);
        setFileName('TallyPrime_Live_DayBook');
        setUploadSt({ loading: true, error: null });
        try {
            await doPreview(targetUrl, null, isAuto);
            setUploadSt(idle());
        } catch (err: any) {
            setUploadSt({ loading: false, error: err.message || 'Failed to fetch DayBook from Tally.' });
        }
    };

    const doTestShopify = async () => {
        if (!shopifyStore.trim() || !shopifyToken.trim()) {
            setShopifyTestMsg({ status: 'error', text: 'Please provide both Store Name and Admin API Access Token.' });
            return;
        }
        setIsTestingShopify(true);
        setShopifyTestMsg(null);
        try {
            const res = await fetch('/api/sources/shopify/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    shop_name: shopifyStore.trim(),
                    access_token: shopifyToken.trim(),
                    resource: shopifyResource
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'Connection failed');
            setShopifyTestMsg({ status: 'success', text: `Connected to store '${shopifyStore}'! Previewed ${data.total_preview_rows} ${shopifyResource} records.` });
        } catch (err: any) {
            setShopifyTestMsg({ status: 'error', text: err.message || 'Shopify connection failed.' });
        } finally {
            setIsTestingShopify(false);
        }
    };

    const doConnectShopify = async (overrideAuto?: boolean) => {
        if (!shopifyStore.trim() || !shopifyToken.trim()) {
            setUploadSt({ loading: false, error: 'Please enter store name and access token.' });
            return;
        }
        const isAuto = overrideAuto ?? autoProceed;
        const cleanStore = shopifyStore.replace('.myshopify.com', '').trim();
        setUploadSt({ loading: true, error: null });
        try {
            const response = await fetch('/api/sources/shopify/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ shop_name: cleanStore, access_token: shopifyToken.trim(), resource: shopifyResource })
            });
            if (!response.ok) throw new Error(await response.text());
            const { file_path: shopifyUri } = await response.json();
            setFilePath(shopifyUri);
            setFileName(`Shopify_${cleanStore}_${shopifyResource}`);
            await doPreview(shopifyUri, null, isAuto);
            setUploadSt(idle());
        } catch (err: any) {
            setUploadSt({ loading: false, error: err.message || 'Failed to fetch data from Shopify.' });
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
                total_rows: data.total_rows || (data.data ? data.data.length : 0),
                connector_warnings: data.connector_warnings || [],
                canonical_fields: data.canonical_fields || [],
                mapping_confirmed: Boolean(data.saved_profile)
            });

            const proposedMap: Record<string, string> = {};
            if (data.mapping_proposal && data.mapping_proposal.suggestions) {
                data.mapping_proposal.suggestions.forEach((s: any) => {
                    if (s.canonical_field) proposedMap[s.source_column] = s.canonical_field;
                });
            }
            const selectedMap = data.saved_profile?.mapping || proposedMap;
            setMapping(selectedMap);

            setPreviewSt(idle());

            if (isAuto) {
                await delay(400);
                if (data.saved_profile) await doMapping(currentFp, selectedMap, currentSheet, isAuto);
                else setStep(2);
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

        if (sourceType === 'sql') {
            await doIngestSingleSqlTable(currentMap);
            return;
        }
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

            const problemStrings = (data.problems || []).map((p: any) => {
                if (!p) return '';
                if (typeof p === 'string') return p;
                return p.message || p.description || p.problem || (typeof p === 'object' ? JSON.stringify(p) : String(p));
            }).filter(Boolean);

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
    const [strategy, setStrategy] = useState<'merge' | 'row'>('row');

    const doIngest = async (targetFp?: string, targetMap?: Record<string, string>, targetSheet?: string | null, targetStrategy?: 'merge' | 'row') => {
        const currentFp = targetFp || filePath;
        const currentMap = targetMap || mapping;
        const currentSheet = targetSheet !== undefined ? targetSheet : sheetName;
        const currentStrategy = targetStrategy || strategy;

        setIngestSt({ loading: true, error: null });
        setStep(5);

        const estimatedTotal = previewData?.total_rows || 100;
        setIngestProgress({
            percent: 15,
            currentChunks: 0,
            totalChunks: estimatedTotal,
            stepText: `Ingesting chunks: 0 / ${estimatedTotal.toLocaleString()} (15%)...`
        });

        // Live progress poller for single-file ingestion
        const pollInterval = setInterval(async () => {
            try {
                const pRes = await fetch(`/api/kb/ingest-progress?file_path=${encodeURIComponent(currentFp)}`);
                if (pRes.ok) {
                    const pData = await pRes.json();
                    if (pData && pData.status === 'active') {
                        clearInterval(pollInterval);
                        setIngestProgress(null);
                        setIngestMsg(pData.step_text || 'Completed');
                        setActivePath(currentFp);
                        setIngestSt(idle());
                        setStep(6);
                        markDataChanged();
                        void fetchKBStats();
                    } else if (pData && pData.status === 'processing') {
                        const stepStr = pData.step_text || '';
                        const match = stepStr.match(/(\d+)\s*\/\s*(\d+)/);
                        let cur = match ? parseInt(match[1], 10) : 0;
                        let tot = match ? parseInt(match[2], 10) : estimatedTotal;
                        let pct = typeof pData.progress === 'number' && pData.progress > 0 ? pData.progress : 15;
                        if (!match && pct > 0) {
                            cur = Math.round((pct / 100) * tot);
                        }
                        setIngestProgress({
                            percent: pct,
                            currentChunks: cur,
                            totalChunks: tot,
                            stepText: stepStr || `Ingesting chunks: ${cur} / ${tot} (${Math.round(pct)}%)...`
                        });
                    } else if (pData && pData.status === 'failed') {
                        clearInterval(pollInterval);
                        setIngestProgress(null);
                        setIngestSt({ loading: false, error: pData.error_message || 'Ingestion was interrupted. Please retry.' });
                    }
                }
            } catch {}
        }, 500);

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
            markDataChanged();
            void fetchKBStats();
        } catch (e: unknown) {
            setIngestSt({ loading: false, error: String((e as Error).message ?? 'Ingest failed.') });
        } finally {
            clearInterval(pollInterval);
        }
    };
    const handleStartChatting = () => {
        if (sourceType === 'sql' && discoveredDb?.database_name) {
            const dbN = discoveredDb.database_name;
            navigate('/chat', {
                state: {
                    dbName: dbN,
                    domain
                }
            });
        } else if (filePath) {
            navigate('/chat', {
                state: {
                    filePath,
                    fileName,
                    domain
                }
            });
        } else if (fileName) {
            navigate('/chat', {
                state: {
                    fileName,
                    domain
                }
            });
        } else {
            navigate('/chat');
        }
    };

    // ── Computed: indicator uses clean 0-6 integer ────────────────
    const indicatorStep =
        step === 0 ? 0
        : step === 1 ? 1
        : step === 2 || step === 3 ? 2
        : step === 4 ? 3
        : step === 5 ? (ingestSt.loading || ingestProgress ? 5 : 4)
        : 6;

    // ==============================================================
    //  RENDER
    // ==============================================================
    return (
        <div className="connect-page">

                {/* ── Header with Clean Action Toolbar & Execution Mode Toggle ── */}
                <div className="connect-page-header">
                    <div className="connect-page-header-text">
                        <h2>Connect Data Source</h2>
                        <p>Ingest and vectorize datasets into your local knowledge base.</p>
                    </div>

                    <div className="connect-header-actions">
                        {step > 0 && (
                            <div className="session-actions-group">
                                <button
                                    type="button"
                                    className="btn-header-action"
                                    onClick={() => resetConnectSession('file')}
                                    title="Upload another file"
                                >
                                    <Upload size={13} /> Upload File
                                </button>
                                <button
                                    type="button"
                                    className="btn-header-action"
                                    onClick={() => resetConnectSession('sql')}
                                    title="Connect a database instead"
                                >
                                    <HardDrive size={13} /> Connect DB
                                </button>
                                <button
                                    type="button"
                                    className="btn-header-action btn-header-reset"
                                    onClick={() => resetConnectSession()}
                                    title="Reset current session"
                                >
                                    <RotateCcw size={13} /> Reset
                                </button>
                            </div>
                        )}

                        <div className="proceed-mode-toggle">
                            <button
                                type="button"
                                className={`mode-btn ${!autoProceed ? 'active' : ''}`}
                                onClick={() => setAutoProceed(false)}
                                title="Manual step-by-step verification"
                            >
                                <Play size={12} /> Manual
                            </button>
                            <button
                                type="button"
                                className={`mode-btn ${autoProceed ? 'active' : ''}`}
                                onClick={() => setAutoProceed(true)}
                                title="Automatically proceed through pipeline"
                            >
                                <Zap size={12} /> Auto Proceed
                            </button>
                        </div>
                    </div>
                </div>

                {/* ── Auto proceed active status chip ──────────────── */}
                {autoProceed && (
                    <div className="auto-proceed-banner">
                        <Zap size={14} />
                        <span>Auto Proceed Active — pipeline steps will advance automatically</span>
                    </div>
                )}

                {/* ── Progress indicator ─────────────────────── */}
                <StepIndicator currentStep={indicatorStep} />

                {/* ════════════════════════════════════════════
                    STEP 1 — CONNECT DATA SOURCE
                ════════════════════════════════════════════ */}
                <div className="wizard-card step-0-card" id="wizard-step-0">
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
                        {(domain === 'pharmacy' || domain === 'finance') && (
                            <button
                                type="button"
                                className={`source-tab-btn ${sourceType === 'tally' ? 'active' : ''}`}
                                onClick={() => {
                                    if (step > 0 && sourceType !== 'tally') resetConnectSession('tally');
                                    else setSourceType('tally');
                                }}
                            >
                                <RefreshCw size={14} /> Tally Prime / ERP 9 (Live XML)
                            </button>
                        )}
                        {domain === 'ecommerce' && (
                            <button
                                type="button"
                                className={`source-tab-btn ${sourceType === 'shopify' ? 'active' : ''}`}
                                onClick={() => {
                                    if (step > 0 && sourceType !== 'shopify') resetConnectSession('shopify');
                                    else setSourceType('shopify');
                                }}
                            >
                                <ShoppingBag size={14} /> Shopify Store Connection
                            </button>
                        )}
                    </div>

                    <div className="wizard-card-title"><Upload size={16} /> Step 1 — Connect Source</div>

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
                                        onClick={() => doUpload(autoProceed)}
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
                                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                    <label>Connection String or File Path:</label>
                                    {isScanningLocalSql && (
                                        <span style={{ fontSize: '0.75rem', color: '#64748B' }}>
                                            <span className="spinner" style={{ width: 10, height: 10 }} /> Scanning local SQL instances…
                                        </span>
                                    )}
                                </div>

                                {localSqlInstances.length > 0 && (
                                    <div className="local-sql-suggestions">
                                        <span style={{ fontSize: '0.78rem', color: '#64748B', fontWeight: 600 }}>⚡ Detected Local Databases:</span>
                                        {localSqlInstances.map(inst => (
                                            <button
                                                key={`${inst.server}-${inst.database_name}`}
                                                type="button"
                                                className="local-sql-chip"
                                                onClick={() => {
                                                    setDbType('mssql');
                                                    setConnString(inst.connection_string);
                                                    void handleConnectDetectedSql(inst.connection_string);
                                                }}
                                                title={`Click to connect to ${inst.database_name} on ${inst.server}`}
                                            >
                                                <Sparkles size={12} /> {inst.database_name} ({inst.server})
                                            </button>
                                        ))}
                                    </div>
                                )}

                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder={
                                        dbType === 'sqlite'
                                            ? 'C:/POS_Software/pharmacy.db'
                                            : dbType === 'mssql'
                                            ? 'C:/.../PharmacyPOS.mdf or .\\SQLEXPRESS/PharmacyPOS'
                                            : dbType === 'postgresql'
                                            ? 'postgresql://postgres:password@localhost:5432/pharmacy_pos'
                                            : 'mysql+pymysql://root:password@localhost:3306/pharmacy_db'
                                    }
                                    value={connString}
                                    onChange={e => {
                                        const val = e.target.value;
                                        setConnString(val);
                                        const lower = val.trim().toLowerCase();
                                        if (lower.startsWith('mssql') || lower.includes('sqlexpress') || lower.includes('driver=') || lower.endsWith('.mdf')) {
                                            setDbType('mssql');
                                        } else if (lower.startsWith('postgres')) {
                                            setDbType('postgresql');
                                        } else if (lower.startsWith('mysql') || lower.startsWith('mariadb')) {
                                            setDbType('mysql');
                                        } else if (lower.startsWith('sqlite') || lower.endsWith('.db') || lower.endsWith('.sqlite') || lower.endsWith('.sqlite3') || lower.endsWith('.stardb')) {
                                            setDbType('sqlite');
                                        }
                                    }}
                                />

                                {connString.trim().toLowerCase().endsWith('.mdf') && (
                                    <div className="sql-mdf-hint">
                                        <Sparkles size={14} color="#0D7377" />
                                        <span>Physical SQL Server MDF file recognized. Will automatically resolve to local SQLEXPRESS instance and discover all tables.</span>
                                    </div>
                                )}
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

                                            {discoveredDb.tables.filter(t => selectedTables.includes(t.table_name) && !t.error).map(table => (
                                                <details key={table.table_name} style={{ marginBottom: '1rem' }}>
                                                    <summary>Review mapping: {table.table_name}</summary>
                                                    <MappingTable columns={table.columns} fields={canonicalFields.length ? canonicalFields : table.canonical_fields} mapping={tableMappings[table.table_name] || {}} onChange={next => setTableMappings(previous => ({ ...previous, [table.table_name]: next }))} />
                                                </details>
                                            ))}
                                            <p>Review the selected table mappings before confirming this import.</p>
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
                                                        <><Zap size={16} /> Confirm Mappings &amp; Import ({selectedTables.length} Tables)</>
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

                    {/* SOURCE 4: TALLY PRIME / ERP 9 */}
                    {sourceType === 'tally' && (
                        <div style={{ marginTop: '0.5rem' }}>
                            <div className="sql-form-group">
                                <label>Tally Server Endpoint URL:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder="http://localhost:9000"
                                    value={tallyUrl}
                                    onChange={e => setTallyUrl(e.target.value)}
                                />
                                <div style={{ fontSize: '0.78rem', color: '#64748B', marginTop: '0.25rem' }}>
                                    Ensure TallyPrime / Tally.ERP 9 is open with ODBC/HTTP listening enabled on port 9000.
                                </div>
                            </div>

                            <div className="sql-form-group">
                                <label>Connection Timeout (seconds):</label>
                                <input
                                    type="number"
                                    className="sql-form-input"
                                    value={tallyTimeout}
                                    onChange={e => setTallyTimeout(parseInt(e.target.value, 10) || 10)}
                                    min={3}
                                    max={60}
                                    style={{ maxWidth: 120 }}
                                />
                            </div>

                            {tallyTestMsg && (
                                <div style={{
                                    padding: '0.6rem 0.8rem',
                                    borderRadius: 6,
                                    marginBottom: '0.75rem',
                                    fontSize: '0.85rem',
                                    backgroundColor: tallyTestMsg.status === 'success' ? '#ECFDF5' : '#FEF2F2',
                                    color: tallyTestMsg.status === 'success' ? '#065F46' : '#991B1B',
                                    border: `1px solid ${tallyTestMsg.status === 'success' ? '#A7F3D0' : '#FECACA'}`
                                }}>
                                    {tallyTestMsg.text}
                                </div>
                            )}

                            {step === 0 && (
                                <div className="btn-actions" style={{ display: 'flex', gap: '0.5rem' }}>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={doTestTally}
                                        disabled={isTestingTally || uploadSt.loading}
                                    >
                                        {isTestingTally ? <><span className="spinner" /> Testing…</> : <><RefreshCw size={14} /> Test Connection</>}
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-primary"
                                        onClick={() => doConnectTally()}
                                        disabled={uploadSt.loading || isTestingTally}
                                    >
                                        {uploadSt.loading ? <><span className="spinner" /> Connecting…</> : autoProceed ? <><Zap size={15} /> Connect &amp; Auto Ingest</> : <><RefreshCw size={15} /> Connect &amp; Preview DayBook</>}
                                    </button>
                                </div>
                            )}

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={() => doConnectTally()} />
                                </div>
                            )}
                        </div>
                    )}

                    {/* SOURCE 5: SHOPIFY STORE */}
                    {sourceType === 'shopify' && (
                        <div style={{ marginTop: '0.5rem' }}>
                            <div className="sql-form-group">
                                <label>Shopify Store Name / Subdomain:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder="my-store (from my-store.myshopify.com)"
                                    value={shopifyStore}
                                    onChange={e => setShopifyStore(e.target.value)}
                                />
                            </div>

                            <div className="sql-form-group">
                                <label>Admin API Access Token:</label>
                                <input
                                    type="password"
                                    className="sql-form-input"
                                    placeholder="shpat_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                                    value={shopifyToken}
                                    onChange={e => setShopifyToken(e.target.value)}
                                />
                                <div style={{ fontSize: '0.78rem', color: '#64748B', marginTop: '0.25rem' }}>
                                    Generated from Shopify Admin &rarr; Settings &rarr; Apps and sales channels &rarr; Develop apps.
                                </div>
                            </div>

                            <div className="sql-form-group">
                                <label>Resource to Extract &amp; Analyze:</label>
                                <select
                                    className="sql-form-input"
                                    value={shopifyResource}
                                    onChange={e => setShopifyResource(e.target.value)}
                                >
                                    <option value="orders">Orders &amp; Line Items (Sales, Discounts, Tax)</option>
                                    <option value="products">Products, Variants &amp; Inventory Quantities</option>
                                    <option value="customers">Customers &amp; Lifetime Value</option>
                                    <option value="reviews">Product Reviews &amp; Ratings</option>
                                </select>
                            </div>

                            {shopifyTestMsg && (
                                <div style={{
                                    padding: '0.6rem 0.8rem',
                                    borderRadius: 6,
                                    marginBottom: '0.75rem',
                                    fontSize: '0.85rem',
                                    backgroundColor: shopifyTestMsg.status === 'success' ? '#ECFDF5' : '#FEF2F2',
                                    color: shopifyTestMsg.status === 'success' ? '#065F46' : '#991B1B',
                                    border: `1px solid ${shopifyTestMsg.status === 'success' ? '#A7F3D0' : '#FECACA'}`
                                }}>
                                    {shopifyTestMsg.text}
                                </div>
                            )}

                            {step === 0 && (
                                <div className="btn-actions" style={{ display: 'flex', gap: '0.5rem' }}>
                                    <button
                                        type="button"
                                        className="btn-secondary"
                                        onClick={doTestShopify}
                                        disabled={isTestingShopify || uploadSt.loading}
                                    >
                                        {isTestingShopify ? <><span className="spinner" /> Testing API…</> : <><ShoppingBag size={14} /> Test Shopify API</>}
                                    </button>
                                    <button
                                        type="button"
                                        className="btn-primary"
                                        onClick={() => doConnectShopify()}
                                        disabled={uploadSt.loading || isTestingShopify || !shopifyStore || !shopifyToken}
                                    >
                                        {uploadSt.loading ? <><span className="spinner" /> Connecting…</> : autoProceed ? <><Zap size={15} /> Connect &amp; Auto Ingest</> : <><ShoppingBag size={15} /> Connect &amp; Preview {shopifyResource.toUpperCase()}</>}
                                    </button>
                                </div>
                            )}

                            {uploadSt.error && (
                                <div style={{ marginTop: '1rem' }}>
                                    <ErrorCard msg={uploadSt.error} onRetry={() => doConnectShopify()} />
                                </div>
                            )}
                        </div>
                    )}

                    {/* SOURCE 3: FOLDER WATCHER */}
                    {sourceType === 'watcher' && (
                        <div style={{ marginTop: '0.5rem' }}>
                            <div className="sql-form-group">
                                <label>Automated Export Directory Path or Database File:</label>
                                <input
                                    type="text"
                                    className="sql-form-input"
                                    placeholder="C:/POS_Exports/, C:/.../PharmacyPOS.mdf, or C:/.../sales.stardb"
                                    value={watchDir}
                                    onChange={e => setWatchDir(e.target.value)}
                                />
                                {watchDir.trim().toLowerCase().endsWith('.mdf') && !detectedSqlDb && (
                                    <div className="sql-mdf-hint">
                                        <Sparkles size={14} color="#0D7377" />
                                        <span>SQL Server Database file (.mdf) detected. Click &quot;Scan Directory&quot; to auto-connect.</span>
                                    </div>
                                )}
                            </div>

                            <div className="btn-actions">
                                <button
                                    className="btn-secondary"
                                    onClick={doCheckWatcher}
                                    disabled={!watchDir || uploadSt.loading || isDiscovering}
                                >
                                    {uploadSt.loading ? (
                                        <><span className="spinner" /> Scanning…</>
                                    ) : (
                                        <><Folder size={15} /> Scan Directory</>
                                    )}
                                </button>
                            </div>

                            {/* Detected Microsoft SQL Server Database Card */}
                            {detectedSqlDb && (
                                <div className="sql-detected-card">
                                    <div className="sql-detected-header">
                                        <div className="sql-detected-icon-wrap">
                                            <Sparkles size={20} />
                                        </div>
                                        <div>
                                            <div className="sql-detected-title">
                                                {detectedSqlDb.server.includes('SQLite') ? 'SQLite / StarDB Database Detected:' : 'Microsoft SQL Server Database Detected:'} <span className="highlight">{detectedSqlDb.database_name}</span>
                                            </div>
                                            <div className="sql-detected-meta">
                                                <span>Instance: <code>{detectedSqlDb.server}</code></span>
                                                <span>·</span>
                                                <span>File: <code>{detectedSqlDb.file_name}</code></span>
                                                <span>·</span>
                                                <span className="badge-online">● Online &amp; Ready</span>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="sql-detected-body">
                                        <div style={{ fontSize: '0.85rem', color: '#334155', marginBottom: '0.5rem' }}>
                                            Found <strong>{detectedSqlDb.table_count} tables</strong> ready for auto-vectorization and schema mapping:
                                        </div>
                                        <div className="sql-detected-tables-preview">
                                            {detectedSqlDb.tables.slice(0, 8).map(t => (
                                                <span key={t} className="db-table-pill">📊 {t}</span>
                                            ))}
                                            {detectedSqlDb.tables.length > 8 && (
                                                <span className="db-table-pill more">+{detectedSqlDb.tables.length - 8} more</span>
                                            )}
                                        </div>
                                    </div>

                                    <div className="sql-detected-actions">
                                        <button
                                            type="button"
                                            className="btn-primary"
                                            onClick={() => handleConnectDetectedSql(detectedSqlDb.file_path)}
                                            disabled={uploadSt.loading || isDiscovering}
                                        >
                                            {isDiscovering ? (
                                                <><span className="spinner" /> Connecting &amp; Scanning Tables…</>
                                            ) : (
                                                <><Zap size={15} /> ⚡ Connect &amp; Ingest {detectedSqlDb.database_name} (1-Click)</>
                                            )}
                                        </button>
                                        <button
                                            type="button"
                                            className="btn-secondary"
                                            onClick={() => {
                                                setSourceType('sql');
                                                setDbType((detectedSqlDb.file_path || '').toLowerCase().endsWith('.stardb') || (detectedSqlDb.file_path || '').toLowerCase().endsWith('.db') || (detectedSqlDb.file_path || '').toLowerCase().endsWith('.sqlite') || (detectedSqlDb.server || '').toLowerCase().includes('sqlite') ? 'sqlite' : 'mssql');
                                                setConnString(detectedSqlDb.file_path);
                                            }}
                                        >
                                            <HardDrive size={14} /> Open in SQL Database Connection
                                        </button>
                                    </div>
                                </div>
                            )}

                            {pendingFiles.length > 0 && !detectedSqlDb && (
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

                            {step === 0 && pendingFiles.length > 0 && !detectedSqlDb && (
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
                            <span>
                                Source connected: <strong>{sourceType === 'sql' ? `${dbType.toUpperCase()} Database` : fileName || 'Dataset file'}</strong>
                            </span>
                        </div>
                    )}
                </div>

                {/* ════════════════════════════════════════════
                    STEP 2 — PREVIEW
                ════════════════════════════════════════════ */}
                {step >= 1 && (
                    <div className="wizard-card step-1-card" id="wizard-step-1">
                        <div className="wizard-card-title"><Eye size={16} /> Step 2 — Preview Data</div>

                        {previewSt.error && <ErrorCard msg={previewSt.error} onRetry={() => doPreview()} />}
                        {previewData?.connector_warnings?.map((warning, index) => (
                            <p key={index} role="status">{warning}</p>
                        ))}

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
                                    {sourceType === 'sql' ? (
                                        <button
                                            className="btn-primary"
                                            onClick={() => setStep(2)}
                                            disabled={isIngestingDb}
                                        >
                                            {isIngestingDb ? <><span className="spinner" /> Ingesting Table…</> : <><GitMerge size={15} /> Review Column Mapping</>}
                                        </button>
                                    ) : (
                                        <button className="btn-primary" onClick={() => setStep(2)}>
                                            {autoProceed ? <Zap size={15} /> : <ArrowRight size={15} />} Review Column Mapping
                                        </button>
                                    )}
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
                                <CheckCircle size={16} /> Preview loaded. Review the column mapping below.
                            </div>
                        )}
                    </div>
                )}

                {/* ════════════════════════════════════════════
                    STEP 3 — MAPPING
                ════════════════════════════════════════════ */}
                {step >= 2 && (
                    <div className="wizard-card step-2-card" id="wizard-step-2">
                        <div className="wizard-card-title"><GitMerge size={16} /> Step 3 — Column Mapping</div>

                        {mappingSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Detecting column mapping…
                            </div>
                        )}

                        {mappingSt.error && <ErrorCard msg={mappingSt.error} onRetry={() => doMapping()} />}

                        {!mappingSt.loading && Boolean(previewData) && (
                            <>
                                <p style={{ fontSize: '0.875rem', color: 'var(--text-secondary)', marginBottom: '1rem' }}>
                                    Review detected column mapping before proceeding.
                                </p>
                                <MappingTable mapping={mapping} columns={previewData?.columns} fields={canonicalFields.length ? canonicalFields : previewData?.canonical_fields || Object.values(mapping)} onChange={next => {
                                    setMapping(next);
                                    setValidateResult(null);
                                    setStep(2);
                                }} />
                                {step === 2 && <div className="btn-actions">
                                    <button className="btn-primary" disabled={mappingSt.loading || Object.keys(mapping).length === 0} onClick={() => doMapping(filePath, mapping, sheetName, autoProceed)}>
                                        Confirm &amp; Save Mapping
                                    </button>
                                </div>}

                                {step === 3 && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={() => doNormalize(filePath, mapping, sheetName, autoProceed)} disabled={normSt.loading}>
                                            {normSt.loading
                                                ? <><span className="spinner" /> Normalizing…</>
                                                : autoProceed
                                                    ? <><Zap size={15} /> Normalize &amp; Auto Process</>
                                                    : <><ArrowRight size={15} /> Normalize Data</>}
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
                    <div className="wizard-card" id="wizard-step-4">
                        <div className="wizard-card-title"><Wrench size={16} /> Step 4 — Normalize Data</div>

                        {normSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: 'var(--text-secondary)' }}>
                                <span className="spinner dark" /> Normalizing data…
                            </div>
                        )}

                        {!normSt.loading && !normSt.error && (
                            <>
                                <div className="info-card">
                                    <CheckCircle size={16} /> Data normalized successfully. File prepared for validation and ingestion.
                                </div>

                                {step === 4 && !valSt.loading && !validateResult && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={() => doValidate(filePath, mapping, sheetName, autoProceed)}>
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
                                                {validateResult.problems.map((p: any, i: number) => (
                                                    <li key={i}>
                                                        <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                                                        <span style={{ marginLeft: '0.5rem' }}>{typeof p === 'string' ? p : p?.message || p?.description || (p ? JSON.stringify(p) : '')}</span>
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
                    <div className="wizard-card" id="wizard-step-5">
                        <div className="wizard-card-title"><Database size={16} /> Step 5 — Ingest to Knowledge Base</div>

                        {validateResult && (validateResult.verdict === 'usable' || validateResult.verdict === 'usable_with_warnings') && step === 5 && !ingestSt.loading && !ingestMsg && (
                            <>
                                {validateResult.verdict === 'usable' ? (
                                    <div className="verdict-badge ok">
                                        <CheckCircle size={16} /> Data validated — ready to ingest into Knowledge Base
                                    </div>
                                ) : (
                                    <>
                                        <div className="verdict-badge warn">
                                            <AlertTriangle size={16} /> Data validated with missing-field warnings — ready to ingest
                                        </div>
                                        {validateResult.problems && validateResult.problems.length > 0 && (
                                            <ul className="problems-list" style={{ marginTop: '0.75rem', marginBottom: '0.75rem', maxHeight: '130px', overflowY: 'auto' }}>
                                                {validateResult.problems.slice(0, 5).map((p: any, i: number) => (
                                                    <li key={i}>
                                                        <AlertTriangle size={13} style={{ flexShrink: 0 }} />
                                                        <span style={{ marginLeft: '0.5rem' }}>{typeof p === 'string' ? p : p?.message || p?.description || (p ? JSON.stringify(p) : '')}</span>
                                                    </li>
                                                ))}
                                                {validateResult.problems.length > 5 && (
                                                    <li style={{ color: 'var(--text-tertiary)', fontStyle: 'italic', fontSize: '0.75rem' }}>
                                                        +{validateResult.problems.length - 5} more non-critical notice(s)
                                                    </li>
                                                )}
                                            </ul>
                                        )}
                                        <div style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', lineHeight: '1.4' }}>
                                            Available rows can still be ingested. The assistant can answer only from fields present in this source and should say when a requested field is missing.
                                        </div>
                                    </>
                                )}

                                <div style={{ marginTop: '1.25rem', marginBottom: '1.25rem', background: 'var(--surface-container)', padding: '1rem', borderRadius: '10px', border: '1px solid var(--border-color)' }}>
                                    <label style={{ fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-primary)', display: 'block', marginBottom: '0.6rem' }}>
                                        ⚡ Ingestion &amp; Chunking Strategy:
                                    </label>
                                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                                        <div
                                            onClick={() => setStrategy('merge')}
                                            style={{
                                                padding: '0.75rem 1rem',
                                                borderRadius: '8px',
                                                border: strategy === 'merge' ? '2px solid var(--brand-green)' : '1px solid var(--border-color)',
                                                background: strategy === 'merge' ? 'var(--brand-green-container)' : 'var(--surface-bg)',
                                                cursor: 'pointer',
                                                transition: 'all 0.15s ease'
                                            }}
                                        >
                                            <div style={{ fontWeight: 600, fontSize: '0.88rem', color: strategy === 'merge' ? 'var(--brand-green-text)' : 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                                                <span>⚡ Merge / Grouping (advanced)</span>
                                                <span style={{ fontSize: '0.7rem', background: 'var(--brand-green)', color: '#FFF', padding: '2px 6px', borderRadius: '10px', fontWeight: 700 }}>5x–10x FASTER</span>
                                            </div>
                                            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.3rem', lineHeight: '1.3' }}>
                                                Groups adjacent rows that share a usable invoice/bill key. Faster for line-item invoices; row-level lookups can be less precise.
                                            </div>
                                        </div>

                                        <div
                                            onClick={() => setStrategy('row')}
                                            style={{
                                                padding: '0.75rem 1rem',
                                                borderRadius: '8px',
                                                border: strategy === 'row' ? '2px solid var(--brand-green)' : '1px solid var(--border-color)',
                                                background: strategy === 'row' ? 'var(--brand-green-container)' : 'var(--surface-bg)',
                                                cursor: 'pointer',
                                                transition: 'all 0.15s ease'
                                            }}
                                        >
                                            <div style={{ fontWeight: 600, fontSize: '0.88rem', color: strategy === 'row' ? 'var(--brand-green-text)' : 'var(--text-primary)' }}>
                                                📄 Row-by-Row (recommended)
                                            </div>
                                            <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', marginTop: '0.3rem', lineHeight: '1.3' }}>
                                                Keeps each record and its identifiers separate for accurate lookups, filters, and citations. Recommended for pharmacy databases and mixed tables.
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
                            <div style={{
                                background: 'var(--surface-container)',
                                border: '1px solid var(--border-color)',
                                borderRadius: '12px',
                                padding: '1.25rem 1.5rem',
                                marginTop: '1rem',
                                animation: 'fadeIn 0.2s ease'
                            }}>
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.85rem', flexWrap: 'wrap', gap: '0.5rem' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                                        <span className="spinner dark" />
                                        <span style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: '0.92rem' }}>
                                            Ingesting data into {domain.toUpperCase()} Knowledge Base...
                                        </span>
                                    </div>
                                    <div style={{
                                        display: 'inline-flex',
                                        alignItems: 'center',
                                        gap: '0.4rem',
                                        background: 'var(--brand-green)',
                                        color: '#FFFFFF',
                                        padding: '0.35rem 0.85rem',
                                        borderRadius: '9999px',
                                        fontSize: '0.78rem',
                                        fontWeight: 600,
                                        boxShadow: '0 1px 3px rgba(16, 185, 129, 0.25)'
                                    }}>
                                        <Zap size={13} />
                                        <span>
                                            {ingestProgress
                                                ? `${ingestProgress.currentChunks.toLocaleString()} / ${ingestProgress.totalChunks.toLocaleString()} chunks ingested (${Math.round(ingestProgress.percent)}%)`
                                                : `0 / ${(previewData?.total_rows || 100).toLocaleString()} chunks ingested (15%)`}
                                        </span>
                                    </div>
                                </div>

                                {/* Progress Bar Track */}
                                <div style={{
                                    width: '100%',
                                    height: '8px',
                                    background: 'var(--surface-container-high)',
                                    borderRadius: '9999px',
                                    overflow: 'hidden',
                                    marginBottom: '0.6rem'
                                }}>
                                    <div style={{
                                        width: `${Math.min(100, Math.max(5, ingestProgress?.percent ?? 15))}%`,
                                        height: '100%',
                                        background: 'linear-gradient(90deg, var(--brand-green), #34D399)',
                                        borderRadius: '9999px',
                                        transition: 'width 0.3s ease'
                                    }} />
                                </div>

                                {/* Bottom Subtitle and Percentage */}
                                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                                    <span>
                                        {ingestProgress?.stepText || `Ingesting chunks: 0 / ${(previewData?.total_rows || 100).toLocaleString()} (15%)...`}
                                    </span>
                                    <span style={{ fontWeight: 600, color: 'var(--brand-green)' }}>
                                        {Math.round(ingestProgress?.percent ?? 15)}%
                                    </span>
                                </div>
                            </div>
                        )}

                        {ingestSt.error && <ErrorCard msg={ingestSt.error} onRetry={() => doIngest()} />}

                        {step >= 6 && !ingestSt.loading && !ingestSt.error && (
                            <div className="success-card" id="wizard-step-6">
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
                                    <div style={{ margin: '1rem 0 1.25rem 0', textAlign: 'left', background: 'var(--surface-container)', borderRadius: '10px', border: '1px solid var(--border-color)', padding: '0.85rem', maxHeight: '200px', overflowY: 'auto' }}>
                                        <div style={{ fontSize: '0.78rem', fontWeight: 700, color: 'var(--text-secondary)', marginBottom: '0.5rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                                            Table Ingestion Breakdown:
                                        </div>
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                                            {dbIngestSummary.table_results.map((t, idx) => (
                                                <div key={idx} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.82rem', padding: '6px 10px', borderRadius: '6px', background: 'var(--surface-bg)', border: '1px solid var(--border-color)' }}>
                                                    <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>📊 {t.table_name}</span>
                                                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                                                        <span style={{ color: 'var(--text-secondary)', fontSize: '0.75rem' }}>{t.rows} rows · {t.chunks} chunks</span>
                                                        <span style={{
                                                            fontSize: '0.72rem',
                                                            fontWeight: 600,
                                                            padding: '2px 7px',
                                                            borderRadius: '999px',
                                                            background: t.status === 'unchanged'
                                                                ? 'rgba(59, 130, 246, 0.15)'
                                                                : (t.status === 'success' || t.status === 'synced')
                                                                ? 'var(--brand-green-container)'
                                                                : t.status === 'empty'
                                                                ? 'var(--surface-container-high)'
                                                                : 'rgba(239, 68, 68, 0.15)',
                                                            color: t.status === 'unchanged'
                                                                ? '#2563EB'
                                                                : (t.status === 'success' || t.status === 'synced')
                                                                ? 'var(--brand-green-text)'
                                                                : t.status === 'empty'
                                                                ? 'var(--text-secondary)'
                                                                : '#f87171'
                                                        }}>
                                                            {t.status === 'unchanged'
                                                                ? 'Up-to-Date (0 changes)'
                                                                : (t.status === 'success' || t.status === 'synced')
                                                                ? (t.new_rows || t.deleted_rows || t.updated_rows
                                                                    ? `Synced (+${t.new_rows ?? 0} / -${t.deleted_rows ?? 0})`
                                                                    : 'Ingested')
                                                                : t.status === 'empty'
                                                                ? 'Empty (0 rows)'
                                                                : 'Failed'}
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
                                    <button className="btn-primary" onClick={handleStartChatting}>
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
    );
}

export default function ConnectSource() {
    return (
        <ErrorBoundary>
            <ConnectSourceContent />
        </ErrorBoundary>
    );
}
