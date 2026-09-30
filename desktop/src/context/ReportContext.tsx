import React, { createContext, useContext, useState, useEffect, type ReactNode, useCallback } from 'react';
import { useUser } from './UserContext';
import { useFilePath } from './FileContext';

export interface VerifiedClaim {
    matched_text: string;
    extracted_value: number;
    matched_kpi_key: string | null;
    status: 'verified' | 'mismatch' | 'unmatched';
    expected_value: number | null;
    reason: string | null;
}

export interface VerificationReport {
    all_verified: boolean;
    verified_count: number;
    unmatched_count: number;
    mismatch_count: number;
    claims: VerifiedClaim[];
}

export interface GeneratedReportPayload {
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

export interface ReportApiResponse {
    report: GeneratedReportPayload;
    download_url_html?: string;
    download_url_pdf?: string;
    download_url?: string;
    domain: string;
    source: string;
}

interface ReportContextType {
    // Weekly Report State
    weeklyIsGenerating: boolean;
    weeklyGenerationStep: string;
    weeklyGenerationProgress: number;
    weeklyError: string | null;
    weeklyResult: ReportApiResponse | null;
    setWeeklyResult: (val: ReportApiResponse | null) => void;
    weeklySelectedFile: string;
    setWeeklySelectedFile: (file: string) => void;
    weeklyBusinessName: string;
    setWeeklyBusinessName: (name: string) => void;
    weeklyFormatPdf: boolean;
    setWeeklyFormatPdf: (val: boolean) => void;
    weeklyFormatHtml: boolean;
    setWeeklyFormatHtml: (val: boolean) => void;
    weeklyAllowRetry: boolean;
    setWeeklyAllowRetry: (val: boolean) => void;
    weeklyPreviewMode: 'html' | 'pdf' | 'audit';
    setWeeklyPreviewMode: (mode: 'html' | 'pdf' | 'audit') => void;
    generateWeeklyReport: (overrideFilePath?: string) => Promise<void>;
    clearWeeklyResult: () => void;

    // Export Standard Report State
    exportIsGenerating: boolean;
    exportGenerationStep: string;
    exportGenerationProgress: number;
    exportError: string | null;
    exportResult: ReportApiResponse | null;
    setExportResult: (val: ReportApiResponse | null) => void;
    exportSelectedFile: string;
    setExportSelectedFile: (file: string) => void;
    exportBusinessName: string;
    setExportBusinessName: (name: string) => void;
    exportFormatPdf: boolean;
    setExportFormatPdf: (val: boolean) => void;
    exportFormatHtml: boolean;
    setExportFormatHtml: (val: boolean) => void;
    exportAllowRetry: boolean;
    setExportAllowRetry: (val: boolean) => void;
    exportPreviewMode: 'html' | 'pdf' | 'audit';
    setExportPreviewMode: (mode: 'html' | 'pdf' | 'audit') => void;
    generateExportReport: (overrideFilePath?: string) => Promise<void>;
    clearExportResult: () => void;

    // Global Indicator
    isAnyReportGenerating: boolean;
    activeGeneratingType: 'weekly' | 'export' | null;
    activeGeneratingStep: string;
}

const ReportContext = createContext<ReportContextType | undefined>(undefined);

const API_BASE = '';
const SESSION_KEY_WEEKLY = 'llm_konnect_weekly_report_cache';
const SESSION_KEY_EXPORT = 'llm_konnect_export_report_cache';
const SESSION_KEY_WEEKLY_SOURCE = 'llm_konnect_weekly_report_source';

type ProgressUpdate = { progress: number; stage: string; status: string };

async function postReportWithProgress(payload: Record<string, unknown>, onProgress: (update: ProgressUpdate) => void): Promise<ReportApiResponse> {
    const generationId = typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID() : `report-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    let polling = false;
    const poll = async () => {
        if (polling) return;
        polling = true;
        try { const response = await fetch(`${API_BASE}/api/report/progress/${encodeURIComponent(generationId)}`); if (response.ok) onProgress(await response.json()); }
        catch { /* The generation POST remains authoritative if polling is interrupted. */ }
        finally { polling = false; }
    };
    const timer = window.setInterval(() => { void poll(); }, 700);
    try {
        const response = await fetch(`${API_BASE}/api/report/generate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...payload, generation_id: generationId }) });
        if (!response.ok) { const errData = await response.json().catch(() => ({})); throw new Error(errData.detail || `Server error (${response.status})`); }
        const data: ReportApiResponse = await response.json(); onProgress({ progress: 100, stage: 'Report ready', status: 'complete' }); return data;
    } finally { window.clearInterval(timer); }
}

export const ReportProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
    const { user } = useUser();
    const { activePath } = useFilePath();

    // ── Weekly Report States ──────────────────────────────────────────
    const [weeklyIsGenerating, setWeeklyIsGenerating] = useState<boolean>(false);
    const [weeklyGenerationStep, setWeeklyGenerationStep] = useState<string>('');
    const [weeklyGenerationProgress, setWeeklyGenerationProgress] = useState<number>(0);
    const [weeklyError, setWeeklyError] = useState<string | null>(null);
    const [weeklyResult, setWeeklyResult] = useState<ReportApiResponse | null>(() => {
        try {
            const cached = sessionStorage.getItem(SESSION_KEY_WEEKLY);
            return cached ? JSON.parse(cached) : null;
        } catch {
            return null;
        }
    });
    const [weeklySelectedFile, setWeeklySelectedFile] = useState<string>(() => {
        try { return sessionStorage.getItem(SESSION_KEY_WEEKLY_SOURCE) || ''; } catch { return ''; }
    });
    const [weeklyBusinessName, setWeeklyBusinessName] = useState<string>('Fazal Din & Sons Pharmacy');
    const [weeklyFormatPdf, setWeeklyFormatPdf] = useState<boolean>(true);
    const [weeklyFormatHtml, setWeeklyFormatHtml] = useState<boolean>(true);
    const [weeklyAllowRetry, setWeeklyAllowRetry] = useState<boolean>(true);
    const [weeklyPreviewMode, setWeeklyPreviewMode] = useState<'html' | 'pdf' | 'audit'>('pdf');

    // ── Standard Export Report States ─────────────────────────────────
    const [exportIsGenerating, setExportIsGenerating] = useState<boolean>(false);
    const [exportGenerationStep, setExportGenerationStep] = useState<string>('');
    const [exportGenerationProgress, setExportGenerationProgress] = useState<number>(0);
    const [exportError, setExportError] = useState<string | null>(null);
    const [exportResult, setExportResult] = useState<ReportApiResponse | null>(() => {
        try {
            const cached = sessionStorage.getItem(SESSION_KEY_EXPORT);
            return cached ? JSON.parse(cached) : null;
        } catch {
            return null;
        }
    });
    const [exportSelectedFile, setExportSelectedFile] = useState<string>('');
    const [exportBusinessName, setExportBusinessName] = useState<string>('Al-Shifa Family Pharmacy');
    const [exportFormatPdf, setExportFormatPdf] = useState<boolean>(true);
    const [exportFormatHtml, setExportFormatHtml] = useState<boolean>(true);
    const [exportAllowRetry, setExportAllowRetry] = useState<boolean>(true);
    const [exportPreviewMode, setExportPreviewMode] = useState<'html' | 'pdf' | 'audit'>('pdf');

    // Sync results with session storage
    useEffect(() => {
        try {
            if (weeklyResult) {
                sessionStorage.setItem(SESSION_KEY_WEEKLY, JSON.stringify(weeklyResult));
            } else {
                sessionStorage.removeItem(SESSION_KEY_WEEKLY);
            }
        } catch {}
    }, [weeklyResult]);

    useEffect(() => {
        try {
            if (exportResult) {
                sessionStorage.setItem(SESSION_KEY_EXPORT, JSON.stringify(exportResult));
            } else {
                sessionStorage.removeItem(SESSION_KEY_EXPORT);
            }
        } catch {}
    }, [exportResult]);

    useEffect(() => {
        try {
            if (weeklySelectedFile) sessionStorage.setItem(SESSION_KEY_WEEKLY_SOURCE, weeklySelectedFile);
            else sessionStorage.removeItem(SESSION_KEY_WEEKLY_SOURCE);
        } catch {}
    }, [weeklySelectedFile]);

    // Keep selected file aligned with activePath if present
    useEffect(() => {
        if (activePath) {
            if (!weeklySelectedFile) setWeeklySelectedFile(activePath);
            if (!exportSelectedFile) setExportSelectedFile(activePath);
        }
    }, [activePath]);

    // ── Weekly Report Generator ───────────────────────────────────────
    const generateWeeklyReport = useCallback(async (overrideFilePath?: string) => {
        const fileToUse = overrideFilePath || weeklySelectedFile || activePath;
        if (!fileToUse) {
            setWeeklyError('Please select a data source to generate the weekly report from.');
            return;
        }

        setWeeklyError(null);
        setWeeklyIsGenerating(true);
        setWeeklyGenerationProgress(1);
        const sourceLabel = fileToUse.startsWith('db://') ? 'the selected POS database' : 'the selected workbook';
        setWeeklyGenerationStep(`Reading ${sourceLabel} and mapping columns`);

        const formats: string[] = [];
        if (weeklyFormatHtml) formats.push('html');
        if (weeklyFormatPdf) formats.push('pdf');
        if (formats.length === 0) formats.push('pdf');

        try {
            const currentDomain = user?.domain || 'pharmacy';
            const defaultBiz = currentDomain === 'ecommerce' 
                ? (user?.organization ? `${user.organization} - E-Commerce Weekly Report` : 'E-Commerce Weekly Store Performance Report')
                : (weeklyBusinessName || 'Pharmacy Weekly Executive Report');

            const payload = {
                file_path: fileToUse,
                domain: currentDomain,
                business_name: weeklyBusinessName || defaultBiz,
                report_type: currentDomain === 'pharmacy' ? 'weekly_pharmacy' : 'standard',
                max_regeneration_attempts: weeklyAllowRetry ? 1 : 0,
                formats: formats
            };

            const data = await postReportWithProgress(payload, update => {
                setWeeklyGenerationProgress(update.progress);
                setWeeklyGenerationStep(update.stage);
            });
            setWeeklyResult(data);
            if (data.download_url_pdf) {
                setWeeklyPreviewMode('pdf');
            } else if (data.download_url_html) {
                setWeeklyPreviewMode('html');
            }
        } catch (err: any) {
            setWeeklyError(err.message || 'Failed to generate weekly report. Please check backend connection.');
        } finally {
            setWeeklyIsGenerating(false);
            setWeeklyGenerationStep('');
            setWeeklyGenerationProgress(0);
        }
    }, [weeklySelectedFile, activePath, user?.domain, weeklyBusinessName, weeklyAllowRetry, weeklyFormatHtml, weeklyFormatPdf]);

    const clearWeeklyResult = useCallback(() => {
        setWeeklyResult(null);
        setWeeklyError(null);
        sessionStorage.removeItem(SESSION_KEY_WEEKLY);
    }, []);

    // ── Standard Export Report Generator ──────────────────────────────
    const generateExportReport = useCallback(async (overrideFilePath?: string) => {
        const fileToUse = overrideFilePath || exportSelectedFile || activePath;
        if (!fileToUse) {
            setExportError('Please select a data file to generate a report from.');
            return;
        }

        setExportError(null);
        setExportIsGenerating(true);
        setExportGenerationProgress(1);
        setExportGenerationStep('Starting report generation');

        const formats: string[] = [];
        if (exportFormatHtml) formats.push('html');
        if (exportFormatPdf) formats.push('pdf');
        if (formats.length === 0) formats.push('pdf');

        try {
            const payload = {
                file_path: fileToUse,
                domain: user?.domain || 'pharmacy',
                business_name: exportBusinessName || 'Executive Summary Report',
                max_regeneration_attempts: exportAllowRetry ? 1 : 0,
                formats: formats
            };

            const data = await postReportWithProgress(payload, update => {
                setExportGenerationProgress(update.progress);
                setExportGenerationStep(update.stage);
            });
            setExportResult(data);
            if (data.download_url_pdf) {
                setExportPreviewMode('pdf');
            } else if (data.download_url_html) {
                setExportPreviewMode('html');
            }
        } catch (err: any) {
            setExportError(err.message || 'Failed to generate report. Please check backend connection.');
        } finally {
            setExportIsGenerating(false);
            setExportGenerationStep('');
            setExportGenerationProgress(0);
        }
    }, [exportSelectedFile, activePath, user?.domain, exportBusinessName, exportAllowRetry, exportFormatHtml, exportFormatPdf]);

    const clearExportResult = useCallback(() => {
        setExportResult(null);
        setExportError(null);
        sessionStorage.removeItem(SESSION_KEY_EXPORT);
    }, []);

    const isAnyReportGenerating = weeklyIsGenerating || exportIsGenerating;
    const activeGeneratingType = weeklyIsGenerating ? 'weekly' : exportIsGenerating ? 'export' : null;
    const activeGeneratingStep = weeklyIsGenerating ? weeklyGenerationStep : exportIsGenerating ? exportGenerationStep : '';

    return (
        <ReportContext.Provider
            value={{
                weeklyIsGenerating,
                weeklyGenerationStep,
                weeklyGenerationProgress,
                weeklyError,
                weeklyResult,
                setWeeklyResult,
                weeklySelectedFile,
                setWeeklySelectedFile,
                weeklyBusinessName,
                setWeeklyBusinessName,
                weeklyFormatPdf,
                setWeeklyFormatPdf,
                weeklyFormatHtml,
                setWeeklyFormatHtml,
                weeklyAllowRetry,
                setWeeklyAllowRetry,
                weeklyPreviewMode,
                setWeeklyPreviewMode,
                generateWeeklyReport,
                clearWeeklyResult,

                exportIsGenerating,
                exportGenerationStep,
                exportGenerationProgress,
                exportError,
                exportResult,
                setExportResult,
                exportSelectedFile,
                setExportSelectedFile,
                exportBusinessName,
                setExportBusinessName,
                exportFormatPdf,
                setExportFormatPdf,
                exportFormatHtml,
                setExportFormatHtml,
                exportAllowRetry,
                setExportAllowRetry,
                exportPreviewMode,
                setExportPreviewMode,
                generateExportReport,
                clearExportResult,

                isAnyReportGenerating,
                activeGeneratingType,
                activeGeneratingStep
            }}
        >
            {children}
        </ReportContext.Provider>
    );
};

export const useReport = (): ReportContextType => {
    const context = useContext(ReportContext);
    if (!context) {
        throw new Error('useReport must be used within a ReportProvider');
    }
    return context;
};
