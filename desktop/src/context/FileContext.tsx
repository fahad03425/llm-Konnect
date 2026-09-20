import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

export type Verdict = 'usable' | 'usable_with_warnings' | 'not_usable';
export type SourceType = 'file' | 'sql' | 'watcher';

export interface PreviewData {
    columns: string[];
    sample_rows: (string | number | null)[][];
    total_rows: number;
}

export interface ValidateResult {
    verdict: Verdict;
    problems: string[];
    null_counts: Record<string, number>;
}

export interface KBStats {
    total_chunks: number;
    collection_name: string;
}

export type StepState = { loading: boolean; error: string | null };
export const idleStep = (): StepState => ({ loading: false, error: null });

interface FileContextType {
    // Active file selection across app
    activePath: string;
    setActivePath: (path: string) => void;

    // Connect Wizard Global Session (survives route navigation & tab switching)
    step: number;
    setStep: (step: number) => void;
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
            if (raw) return JSON.parse(raw);
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

    const [uploadSt, setUploadSt] = useState<StepState>(idleStep());
    const [previewSt, setPreviewSt] = useState<StepState>(idleStep());
    const [mappingSt, setMappingSt] = useState<StepState>(idleStep());
    const [normSt, setNormSt] = useState<StepState>(idleStep());
    const [valSt, setValSt] = useState<StepState>(idleStep());
    const [ingestSt, setIngestSt] = useState<StepState>(idleStep());

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
        setAutoProceed(false);
        setSheetName(null);
        setSheets([]);
        setMapping({});
        setPreviewData(null);
        setValidateResult(null);
        setIngestMsg('');
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
