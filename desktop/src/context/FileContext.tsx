import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

export type Verdict = 'usable' | 'usable_with_warnings' | 'not_usable';
export type SourceType = 'file' | 'sql' | 'watcher' | 'tally' | 'shopify';

export interface PreviewData {
    canonical_fields?: string[];
    mapping_confirmed?: boolean;
    connector_warnings?: string[];
    columns: string[];
    sample_rows: (string | number | null)[][];
    total_rows: number;
}

export interface ValidateResult {
    verdict: Verdict;
    problems: any[];
    null_counts: Record<string, number>;
}

export interface KBStats {
    total_chunks: number;
    collection_name: string;
}

export interface IngestProgress {
    percent: number;
    currentChunks: number;
    totalChunks: number;
    stepText: string;
}

export type StepState = { loading: boolean; error: string | null };
export const idleStep = (): StepState => ({ loading: false, error: null });

export const KB_DATA_VERSION_KEY = 'llm_konnect_kb_updated';

export function markDataChanged(): void {
    try {
        localStorage.setItem(KB_DATA_VERSION_KEY, Date.now().toString());
        window.dispatchEvent(new CustomEvent('kb:sources-updated'));
    } catch {}
}

export function getDataVersion(): string {
    try {
        return localStorage.getItem(KB_DATA_VERSION_KEY) || '0';
    } catch {
        return '0';
    }
}

interface FileContextType {
    // Active file selection across app
    activePath: string;
    setActivePath: (path: string) => void;

    // Connect Wizard Global Session (survives route navigation & tab switching)
    step: number;
    setStep: (step: number | ((prev: number) => number)) => void;
    file: File | null;
    setFile: (file: File | null) => void;
    fileName: string;
    setFileName: (name: string) => void;
    filePath: string;
    setFilePath: (path: string) => void;
    sourceType: SourceType;
    setSourceType: (type: SourceType) => void;
    autoProceed: boolean;
    setAutoProceed: (auto: boolean) => void;
    sheetName: string | null;
    setSheetName: (name: string | null) => void;
    sheets: string[];
    setSheets: (sheets: string[]) => void;
    mapping: Record<string, string>;
    setMapping: React.Dispatch<React.SetStateAction<Record<string, string>>>;
    previewData: PreviewData | null;
    setPreviewData: (data: PreviewData | null) => void;
    validateResult: ValidateResult | null;
    setValidateResult: (res: ValidateResult | null) => void;
    ingestMsg: string;
    setIngestMsg: (msg: string) => void;
    kbStats: KBStats | null;
    setKbStats: (stats: KBStats | null) => void;

    // Live Ingestion Progress (persists globally across all page navigations)
    ingestProgress: IngestProgress | null;
    setIngestProgress: (p: IngestProgress | null) => void;

    // Step loading/error states
    uploadSt: StepState;
    setUploadSt: (st: StepState) => void;
    previewSt: StepState;
    setPreviewSt: (st: StepState) => void;
    mappingSt: StepState;
    setMappingSt: (st: StepState) => void;
    normSt: StepState;
    setNormSt: (st: StepState) => void;
    valSt: StepState;
    setValSt: (st: StepState) => void;
    ingestSt: StepState;
    setIngestSt: (st: StepState) => void;

    // Reset wizard
    resetConnectSession: (targetType?: SourceType) => void;
    isConnecting: boolean;
}

const STORAGE_KEY = 'llm_konnect_active_file';
const WIZARD_STORAGE_KEY = 'llm_konnect_connect_session';

const FileContext = createContext<FileContextType | null>(null);

export function FileProvider({ children }: { children: ReactNode }) {
    // ── Active file path ─────────────────────────────────────────────
    const [activePath, setActivePathState] = useState<string>(() => {
        try {
            return localStorage.getItem(STORAGE_KEY) || '';
        } catch {
            return '';
        }
    });

    const setActivePath = (path: string) => {
        setActivePathState(path);
        try {
            if (path) {
                localStorage.setItem(STORAGE_KEY, path);
            } else {
                localStorage.removeItem(STORAGE_KEY);
            }
        } catch (e) {
            console.error('Error saving active file path to storage', e);
        }
    };

    // ── Restore saved wizard session from sessionStorage ───────────
    const loadSavedWizardState = () => {
        try {
            const raw = sessionStorage.getItem(WIZARD_STORAGE_KEY);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (parsed && typeof parsed === 'object') {
                    if (parsed.validateResult?.problems && Array.isArray(parsed.validateResult.problems)) {
                        parsed.validateResult.problems = parsed.validateResult.problems
                            .map((p: any) => typeof p === 'string' ? p : p?.message || p?.description || (p ? JSON.stringify(p) : ''))
                            .filter(Boolean);
                    }
                    return parsed;
                }
            }
        } catch {}
        return null;
    };

    const saved = loadSavedWizardState();

    const [step, setStep] = useState<number>(saved?.step ?? 0);
    const [file, setFile] = useState<File | null>(null);
    const [fileName, setFileName] = useState<string>(saved?.fileName ?? '');
    const [filePath, setFilePath] = useState<string>(saved?.filePath ?? '');
    const [sourceType, setSourceType] = useState<SourceType>(saved?.sourceType ?? 'file');
    const [autoProceed, setAutoProceed] = useState<boolean>(saved?.autoProceed ?? false);
    const [sheetName, setSheetName] = useState<string | null>(saved?.sheetName ?? null);
    const [sheets, setSheets] = useState<string[]>(saved?.sheets ?? []);
    const [mapping, setMapping] = useState<Record<string, string>>(saved?.mapping ?? {});
    const [previewData, setPreviewData] = useState<PreviewData | null>(saved?.previewData ?? null);
    const [validateResult, setValidateResult] = useState<ValidateResult | null>(saved?.validateResult ?? null);
    const [ingestMsg, setIngestMsg] = useState<string>(saved?.ingestMsg ?? '');
    const [kbStats, setKbStats] = useState<KBStats | null>(null);

    const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null);

    const [uploadSt, setUploadSt] = useState<StepState>(idleStep());
    const [previewSt, setPreviewSt] = useState<StepState>(idleStep());
    const [mappingSt, setMappingSt] = useState<StepState>(idleStep());
    const [normSt, setNormSt] = useState<StepState>(idleStep());
    const [valSt, setValSt] = useState<StepState>(idleStep());
    const [ingestSt, setIngestSt] = useState<StepState>(idleStep());

    // ── Continuous background ingestion tracking (active across all pages) ────
    useEffect(() => {
        const activeFp = filePath || file?.name;
        if (!activeFp) return;

        let isMounted = true;
        let isChecking = false;

        const checkActiveIngest = async () => {
            if (isChecking) return;
            isChecking = true;
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
                    setStep(prevStep => (prevStep < 5 ? 5 : prevStep));
                    setIngestProgress({
                        percent: pct,
                        currentChunks: cur,
                        totalChunks: tot,
                        stepText: stepStr || `Ingesting chunks: ${cur} / ${tot} (${Math.round(pct)}%)...`
                    });
                } else if (pData && pData.status === 'active' && (ingestSt.loading || ingestProgress)) {
                    setIngestSt(idleStep());
                    setIngestProgress(null);
                    setStep(6);
                    setIngestMsg(pData.step_text || 'Completed');
                    markDataChanged();
                    try {
                        const sRes = await fetch('/api/kb/stats');
                        if (sRes.ok) setKbStats(await sRes.json());
                    } catch {}
                } else if (pData && pData.status === 'failed' && ingestSt.loading) {
                    setIngestSt({ loading: false, error: pData.error_message || 'Ingestion failed.' });
                    setIngestProgress(null);
                }
            } catch {}
            finally {
                isChecking = false;
            }
        };

        const pollInterval = (ingestSt.loading || ingestProgress || step === 5) ? 600 : 3000;
        void checkActiveIngest();
        const interval = setInterval(checkActiveIngest, pollInterval);
        return () => {
            isMounted = false;
            clearInterval(interval);
        };
    }, [filePath, file, step, ingestSt.loading, Boolean(ingestProgress), previewData?.total_rows]);

    // Sync serializable session state to sessionStorage
    useEffect(() => {
        try {
            const stateToSave = {
                step,
                fileName: file?.name || fileName,
                filePath,
                sourceType,
                autoProceed,
                sheetName,
                sheets,
                mapping,
                previewData,
                validateResult,
                ingestMsg
            };
            sessionStorage.setItem(WIZARD_STORAGE_KEY, JSON.stringify(stateToSave));
        } catch {}
    }, [step, file, fileName, filePath, sourceType, autoProceed, sheetName, sheets, mapping, previewData, validateResult, ingestMsg]);

    const resetConnectSession = (targetType?: SourceType) => {
        setStep(0);
        setFile(null);
        setFileName('');
        setFilePath('');
        setSourceType(targetType || 'file');
        setSheetName(null);
        setSheets([]);
        setMapping({});
        setPreviewData(null);
        setValidateResult(null);
        setIngestMsg('');
        setIngestProgress(null);
        setUploadSt(idleStep());
        setPreviewSt(idleStep());
        setMappingSt(idleStep());
        setNormSt(idleStep());
        setValSt(idleStep());
        setIngestSt(idleStep());
        try {
            sessionStorage.removeItem(WIZARD_STORAGE_KEY);
        } catch {}
    };

    const isConnecting = step > 0 && step < 6;

    return (
        <FileContext.Provider
            value={{
                activePath,
                setActivePath,
                step,
                setStep,
                file,
                setFile,
                fileName: file?.name || fileName,
                setFileName,
                filePath,
                setFilePath,
                sourceType,
                setSourceType,
                autoProceed,
                setAutoProceed,
                sheetName,
                setSheetName,
                sheets,
                setSheets,
                mapping,
                setMapping,
                previewData,
                setPreviewData,
                validateResult,
                setValidateResult,
                ingestMsg,
                setIngestMsg,
                kbStats,
                setKbStats,
                ingestProgress,
                setIngestProgress,
                uploadSt,
                setUploadSt,
                previewSt,
                setPreviewSt,
                mappingSt,
                setMappingSt,
                normSt,
                setNormSt,
                valSt,
                setValSt,
                ingestSt,
                setIngestSt,
                resetConnectSession,
                isConnecting
            }}
        >
            {children}
        </FileContext.Provider>
    );
}

export function useFilePath() {
    const context = useContext(FileContext);
    if (!context) {
        throw new Error('useFilePath must be used within a FileProvider');
    }
    return context;
}

export function useConnectSession() {
    return useFilePath();
}
