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
    AlertCircle, CheckCircle, AlertTriangle, ArrowRight, RefreshCw
} from 'lucide-react';
import { StepIndicator } from '../components/connect/StepIndicator';
import { UploadZone } from '../components/connect/UploadZone';
import { PreviewTable } from '../components/connect/PreviewTable';
import { MappingTable } from '../components/connect/MappingTable';
import { KBStatus } from '../components/connect/KBStatus';
import '../Connect.css';

// ---- Types ----
type Verdict = 'usable' | 'usable_with_warnings' | 'not_usable';

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

// Safe checking for problems

// ============================================================
//  Error Boundary — catches any render crash and shows a card
//  instead of a blank page
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
    //  STEP 1 — UPLOAD
    // ==============================================================
    const doUpload = async () => {
        if (!file) return;
        setUploadSt({ loading: true, error: null });

        const form = new FormData();
        form.append('file', file);
        // No Content-Type header: browser sets multipart/form-data + boundary automatically

        try {
            const res = await fetch('/api/sources/upload', { method: 'POST', body: form });
            if (!res.ok) throw new Error(await res.text());

            const data = await res.json();
            const fp: string = data.file_path;   // store the returned file path
            setFilePath(fp);

            // Excel: also fetch available sheet names
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
                        if (sheetList.length > 0) setSheetName(sheetList[0]);
                    }
                } catch { /* non-critical — single-sheet Excel still works */ }
            }

            setUploadSt(idle());
            setStep(1);   // advance to preview
        } catch (e: unknown) {
            setUploadSt({ loading: false, error: String((e as Error).message ?? 'Upload failed.') });
        }
    };

    // ==============================================================
    //  STEP 2 — PREVIEW
    //  FIX: doPreview only loads data, does NOT advance step.
    //  The user sees the table and then clicks "Confirm Preview" to advance.
    // ==============================================================
    const doPreview = async () => {
        setPreviewSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: filePath, sheet_name: sheetName || null })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            // Backend returns data as array of objects, we need array of arrays
            const sampleRows = data.data ? data.data.map((row: any) => (data.columns || []).map((col: string) => row[col])) : [];

            setPreviewData({
                columns: data.columns || [],
                sample_rows: sampleRows,
                total_rows: data.data ? data.data.length : 0
            });

            // Extract mapping proposal if available
            const proposedMap: Record<string, string> = {};
            if (data.mapping_proposal && data.mapping_proposal.suggestions) {
                data.mapping_proposal.suggestions.forEach((s: any) => {
                    if (s.canonical_field) proposedMap[s.source_column] = s.canonical_field;
                });
            }
            // Store mapping from preview so we can show it in step 3
            if (Object.keys(proposedMap).length > 0) {
                setMapping(proposedMap);
            }

            setPreviewSt(idle());
            // Step stays at 1 — user must click "Confirm Preview" to continue
        } catch (e: unknown) {
            setPreviewSt({ loading: false, error: String((e as Error).message ?? 'Preview failed.') });
        }
    };

    // ==============================================================
    //  STEP 3 — MAPPING CONFIRM
    //  Called when user clicks "Confirm Preview". Advances to step 2
    //  (mapping loading), then step 3 (mapping ready) when done.
    // ==============================================================
    const doMapping = async () => {
        setMappingSt({ loading: true, error: null });
        setStep(2);   // show mapping card immediately with spinner
        try {
            const res = await fetch('/api/sources/mapping/confirm', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: filePath,
                    mapping: mapping,      // USE the proposed mapping we stored in preview!
                    domain: 'pharmacy',
                    sheet_name: sheetName || null,
                    table_or_query: null,
                    keep_extras: true,
                    save_profile: true
                })
            });
            if (!res.ok) throw new Error(await res.text());
            // Backend maps the columns but we already have `mapping` in state.
            setMappingSt(idle());
            setStep(3);   // mapping ready — user can now confirm
        } catch (e: unknown) {
            setMappingSt({ loading: false, error: String((e as Error).message ?? 'Mapping failed.') });
        }
    };

    // ==============================================================
    //  STEP 4 — NORMALIZE
    //  Called when user clicks "Confirm Mapping". Updates file_path.
    // ==============================================================
    const doNormalize = async () => {
        setNormSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/normalize', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: filePath,
                    domain: 'pharmacy',
                    mapping: mapping,          // *** pass stored mapping ***
                    sheet_name: sheetName || null,
                    table_or_query: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();
            // UPDATE to normalized path ONLY if it's provided by backend
            if (data.file_path) {
                setFilePath(data.file_path);
            }
            setNormSt(idle());
            setStep(4);   // advance to validate
        } catch (e: unknown) {
            setNormSt({ loading: false, error: String((e as Error).message ?? 'Normalize failed.') });
        }
    };

    // ==============================================================
    //  STEP 5 — VALIDATE
    //  Auto-advances to ingest if verdict === 'usable'.
    //  Shows "Ingest Anyway" button for 'usable_with_warnings'.
    //  Blocks ingest for 'not_usable'.
    // ==============================================================
    const doValidate = async () => {
        setValSt({ loading: true, error: null });
        try {
            const res = await fetch('/api/sources/validate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: filePath,       // normalized path from step 4 (if changed)
                    mapping: mapping,          // stored mapping from step 3
                    domain: 'pharmacy',
                    table_kind: 'auto',
                    sheet_name: sheetName || null,
                    table_or_query: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            const data = await res.json();

            // Map problems to strings if they are objects
            const problemStrings = data.problems?.map((p: any) => typeof p === 'string' ? p : p.description) || [];

            setValidateResult({
                verdict: data.verdict,
                problems: problemStrings,
                null_counts: data.null_counts || {}
            });
            setValSt(idle());
            // Auto-advance only when truly usable — user must confirm otherwise
            if (data.verdict === 'usable') {
                setStep(5);   // show ingest card
            }
            // 'usable_with_warnings' and 'not_usable' stay on step 4 to show verdict
        } catch (e: unknown) {
            setValSt({ loading: false, error: String((e as Error).message ?? 'Validation failed.') });
        }
    };

    // ==============================================================
    //  STEP 6 — INGEST
    //  Uses normalized file_path and stored mapping.
    // ==============================================================
    const doIngest = async () => {
        setIngestSt({ loading: true, error: null });
        setStep(5);   // ensure ingest card is visible
        try {
            const res = await fetch('/api/kb/ingest', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: filePath,       // normalized path
                    domain: 'pharmacy',
                    strategy: 'row',
                    mapping: mapping,          // stored mapping
                    sheet_name: sheetName || null,
                    table_or_query: null,
                    merge_key: null
                })
            });
            if (!res.ok) throw new Error(await res.text());
            // Ingest returns a plain string OR JSON — handle both
            const raw = await res.text();
            let msg = raw;
            try {
                const parsed = JSON.parse(raw);
                if (typeof parsed === 'object' && parsed !== null) {
                    msg = parsed.message || parsed.detail || JSON.stringify(parsed);
                } else {
                    msg = String(parsed);
                }
            } catch { /* it's already a plain string */ }
            setIngestMsg(String(msg));
            setIngestSt(idle());
            setStep(6);           // wizard complete
            void fetchKBStats();  // refresh KB stats card
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

                {/* ── Header ─────────────────────────────────── */}
                <div className="connect-page-header">
                    <h2>Connect Your Data Source</h2>
                    <p>Upload your business data to power AI insights. All processing is local — no data leaves your machine.</p>
                </div>

                {/* ── Progress indicator ─────────────────────── */}
                <StepIndicator currentStep={indicatorStep} />

                {/* ════════════════════════════════════════════
                    STEP 1 — UPLOAD
                ════════════════════════════════════════════ */}
                <div className="wizard-card">
                    <div className="wizard-card-title"><Upload size={16} /> Step 1 — Upload File</div>

                    {/* Drag-and-drop zone */}
                    <UploadZone file={file} onFileChange={resetFile} />

                    {/* Sheet picker (Excel only, multiple sheets) */}
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

                    {/* Error */}
                    {uploadSt.error && (
                        <div style={{ marginTop: '1rem' }}>
                            <ErrorCard msg={uploadSt.error} onRetry={doUpload} />
                        </div>
                    )}

                    {/* Upload button — only when on step 0 */}
                    {step === 0 && (
                        <div className="btn-actions">
                            <button
                                className="btn-primary"
                                onClick={doUpload}
                                disabled={!file || uploadSt.loading}
                            >
                                {uploadSt.loading
                                    ? <><span className="spinner" /> Uploading…</>
                                    : <><Upload size={15} /> Upload &amp; Analyse</>}
                            </button>
                        </div>
                    )}

                    {/* Success confirmation */}
                    {step >= 1 && (
                        <div className="info-card" style={{ marginTop: '1rem' }}>
                            <CheckCircle size={16} />
                            File uploaded successfully. Path stored for all subsequent steps.
                        </div>
                    )}
                </div>

                {/* ════════════════════════════════════════════
                    STEP 2 — PREVIEW
                    Visible from step 1 onward.
                    FIX: doPreview does NOT advance step.
                    User clicks "Confirm Preview" to advance.
                ════════════════════════════════════════════ */}
                {step >= 1 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Eye size={16} /> Step 2 — Preview Data</div>

                        {previewSt.error && <ErrorCard msg={previewSt.error} onRetry={doPreview} />}

                        {/* No data yet → show Load button */}
                        {!previewData && !previewSt.loading && step === 1 && (
                            <div className="btn-actions">
                                <button className="btn-primary" onClick={doPreview}>
                                    <Eye size={15} /> Load Preview
                                </button>
                            </div>
                        )}

                        {/* Loading spinner */}
                        {previewSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Loading preview…
                            </div>
                        )}

                        {/* Data loaded → show table + confirm button */}
                        {previewData && step === 1 && (
                            <>
                                <PreviewTable
                                    columns={previewData.columns}
                                    sampleRows={previewData.sample_rows}
                                    totalRows={previewData.total_rows}
                                />
                                <div className="btn-actions">
                                    {/* Confirm Preview: advances to step 2 and auto-triggers mapping */}
                                    <button className="btn-primary" onClick={doMapping}>
                                        <ArrowRight size={15} /> Confirm Preview &amp; Load Mapping
                                    </button>
                                </div>
                            </>
                        )}

                        {/* Past preview — show completion badge */}
                        {step >= 2 && (
                            <div className="info-card">
                                <CheckCircle size={16} /> Preview confirmed. Column mapping loaded.
                            </div>
                        )}
                    </div>
                )}

                {/* ════════════════════════════════════════════
                    STEP 3 — MAPPING
                    Visible from step 2 onward.
                    Auto-loads when user confirms preview.
                    User clicks "Confirm Mapping" to normalize.
                ════════════════════════════════════════════ */}
                {step >= 2 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><GitMerge size={16} /> Step 3 — Column Mapping</div>

                        {/* Loading spinner while mapping API runs */}
                        {mappingSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Detecting column mapping…
                            </div>
                        )}

                        {mappingSt.error && <ErrorCard msg={mappingSt.error} onRetry={doMapping} />}

                        {/* Mapping table + confirm button */}
                        {!mappingSt.loading && Object.keys(mapping).length > 0 && (
                            <>
                                <p style={{ fontSize: '0.875rem', color: '#6B7280', marginBottom: '1rem' }}>
                                    The system detected the following column mapping. Review and confirm before continuing.
                                </p>
                                <MappingTable mapping={mapping} />

                                {step === 3 && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={doNormalize} disabled={normSt.loading}>
                                            {normSt.loading
                                                ? <><span className="spinner" /> Normalizing…</>
                                                : <><ArrowRight size={15} /> Confirm Mapping &amp; Normalize</>}
                                        </button>
                                    </div>
                                )}

                                {normSt.error && (
                                    <div style={{ marginTop: '1rem' }}>
                                        <ErrorCard msg={normSt.error} onRetry={doNormalize} />
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
                    Visible from step 4 onward (normalize was triggered in step 3).
                ════════════════════════════════════════════ */}
                {step >= 4 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Wrench size={16} /> Step 4 — Normalize</div>

                        {normSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Normalizing data…
                            </div>
                        )}

                        {/* Success: normalize done */}
                        {!normSt.loading && !normSt.error && (
                            <>
                                <div className="info-card">
                                    <CheckCircle size={16} /> Data normalized. File path updated for validation and ingestion.
                                </div>

                                {/* Validate button — only show when waiting to validate */}
                                {step === 4 && !valSt.loading && !validateResult && (
                                    <div className="btn-actions">
                                        <button className="btn-primary" onClick={doValidate}>
                                            <ShieldCheck size={15} /> Run Validation
                                        </button>
                                    </div>
                                )}

                                {/* Validate loading */}
                                {valSt.loading && (
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280', marginTop: '1rem' }}>
                                        <span className="spinner dark" /> Validating…
                                    </div>
                                )}

                                {valSt.error && (
                                    <div style={{ marginTop: '1rem' }}>
                                        <ErrorCard msg={valSt.error} onRetry={doValidate} />
                                    </div>
                                )}

                                {/* Validate result — shown on step 4 for warnings/fail; step auto-advances to 5 for 'usable' */}
                                {validateResult && step === 4 && (
                                    <div style={{ marginTop: '1.25rem' }}>
                                        {/* Verdict badge */}
                                        <div className={`verdict-badge ${validateResult.verdict === 'usable_with_warnings' ? 'warn' : 'fail'
                                            }`}>
                                            {validateResult.verdict === 'usable_with_warnings'
                                                ? <><AlertTriangle size={16} /> Data is usable — with warnings</>
                                                : <><AlertCircle size={16} /> Data cannot be ingested</>}
                                        </div>

                                        {/* Problems list */}
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
                                                <button className="btn-warn" onClick={doIngest}>
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
                    Visible from step 5 onward.
                    Shows green verdict + Ingest button (or auto-ingests).
                ════════════════════════════════════════════ */}
                {step >= 5 && (
                    <div className="wizard-card">
                        <div className="wizard-card-title"><Database size={16} /> Step 5/6 — Ingest to Knowledge Base</div>

                        {/* Validate: usable badge */}
                        {validateResult && validateResult.verdict === 'usable' && step === 5 && !ingestSt.loading && !ingestMsg && (
                            <>
                                <div className="verdict-badge ok">
                                    <CheckCircle size={16} /> Data validated — ready to ingest
                                </div>
                                <div className="btn-actions">
                                    <button className="btn-primary" onClick={doIngest}>
                                        <Database size={15} /> Ingest into Knowledge Base
                                    </button>
                                </div>
                            </>
                        )}

                        {/* Ingest loading */}
                        {ingestSt.loading && (
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', color: '#6B7280' }}>
                                <span className="spinner dark" /> Ingesting data into the knowledge base…
                            </div>
                        )}

                        {ingestSt.error && <ErrorCard msg={ingestSt.error} onRetry={doIngest} />}

                        {/* Success */}
                        {step >= 6 && !ingestSt.loading && !ingestSt.error && (
                            <div className="success-card">
                                <div className="success-card-icon"><CheckCircle size={26} /></div>
                                <h3>Data Successfully Ingested!</h3>
                                <p>{(typeof ingestMsg === 'string' && ingestMsg && !ingestMsg.startsWith('{')) ? ingestMsg : 'Your data is ready. The RAG chatbot is now powered by this dataset.'}</p>
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
