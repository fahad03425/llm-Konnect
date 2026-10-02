import { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import {
    DollarSign, TrendingUp, BarChart2, Receipt, CreditCard, AlertTriangle,
    Database, Activity, Minus, ShoppingBag, Briefcase, ArrowRight, Calendar,
    RefreshCw, ChevronDown, Check, FileSpreadsheet, Table2, Boxes
} from 'lucide-react';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { useFilePath, getDataVersion } from '../context/FileContext';
import { useUser } from '../context/UserContext';
import { getCustomDbGroups, type CustomDbGroup } from '../utils/dbGroups';
import './Dashboard.css';

interface DatasetOption {
    name: string;
    path: string;
    kind: 'whole_db' | 'table' | 'file';
    dbName?: string;
    isCustomGroup?: boolean;
    customGroupName?: string;
}

// ----------------------------------------------------------------------
// Types based on the User Specifications
// ----------------------------------------------------------------------

interface KPIValue {
    name?: string;
    value?: number;
    unit?: string;
    status: 'ok' | 'unavailable';
    formula?: string;
    reason?: string;
}

interface TrendDataPoint {
    period: string;
    revenue: number;
    transaction_count: number;
}

interface KBStats {
    total_chunks: number;
    collection_name: string;
}

// Helper for fetch with timeout
const fetchWithTimeout = async (url: string, options: RequestInit = {}) => {
    const timeout = 30000;
    const controller = new AbortController();
    const id = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(id);
        return response;
    } catch (e: any) {
        clearTimeout(id);
        if (e.name === 'AbortError') throw new Error(`Request to ${url} timed out after 30s`);
        throw e;
    }
};

// ----------------------------------------------------------------------
// Session Dashboard Cache (Preserves metrics across route navigation)
// Only invalidates when underlying data changes or user requests refresh
// ----------------------------------------------------------------------

interface DashboardCacheEntry {
    kpis: Record<string, KPIValue> | null;
    expiry: Record<string, KPIValue> | null;
    trend: TrendDataPoint[] | null;
    rangeTotalRevenue?: number | null;
    trendRange?: '7d' | '28d' | '6m' | 'all';
    trendGranularity?: 'daily' | 'monthly';
    kbStats: KBStats | null;
    timestamp: number;
    dataVersion: string;
}

const DASHBOARD_CACHE_KEY = 'llm_konnect_dashboard_cache_v1';

function getDashboardCache(): Record<string, DashboardCacheEntry> {
    try {
        const raw = sessionStorage.getItem(DASHBOARD_CACHE_KEY);
        if (raw) return JSON.parse(raw);
    } catch {}
    return {};
}

function getCachedEntry(key: string): DashboardCacheEntry | null {
    const cache = getDashboardCache();
    return cache[key] || null;
}

function saveCachedEntry(key: string, patch: Partial<DashboardCacheEntry>) {
    try {
        const cache = getDashboardCache();
        const existing = cache[key] || {
            kpis: null,
            expiry: null,
            trend: null,
            rangeTotalRevenue: null,
            trendRange: '28d',
            trendGranularity: 'daily',
            kbStats: null,
            timestamp: Date.now(),
            dataVersion: getDataVersion()
        };
        cache[key] = {
            ...existing,
            ...patch,
            timestamp: Date.now(),
            dataVersion: patch.dataVersion !== undefined ? patch.dataVersion : (existing.dataVersion || getDataVersion())
        };
        sessionStorage.setItem(DASHBOARD_CACHE_KEY, JSON.stringify(cache));
    } catch {}
}

// Helper Components
// ----------------------------------------------------------------------

const KPISkeleton = () => <div className="skeleton skeleton-kpi" />;
const ChartSkeleton = () => <div className="skeleton skeleton-chart" />;

// ----------------------------------------------------------------------
// Main Dashboard
// ----------------------------------------------------------------------

export default function Dashboard() {
    const { activePath, setActivePath } = useFilePath();
    const { user, activeDomainMeta } = useUser();
    const cacheKey = `${activePath || ''}:${user.domain}`;
    const currentVersion = getDataVersion();
    const cached = getCachedEntry(cacheKey);
    const isCacheFresh = Boolean(cached && cached.dataVersion === currentVersion);

    const [kpis, setKpis] = useState<Record<string, KPIValue> | null>(() => isCacheFresh ? cached!.kpis : null);
    const [expiry, setExpiry] = useState<Record<string, KPIValue> | null>(() => isCacheFresh ? cached!.expiry : null);
    const [trend, setTrend] = useState<TrendDataPoint[] | null>(() => isCacheFresh ? cached!.trend : null);
    const [kbStats, setKbStats] = useState<KBStats | null>(() => isCacheFresh ? cached!.kbStats : null);

    const [trendRange, setTrendRange] = useState<'7d' | '28d' | '6m' | 'all'>(() => (isCacheFresh && cached!.trendRange) ? cached!.trendRange : '28d');
    const [trendGranularity, setTrendGranularity] = useState<'daily' | 'monthly'>(() => (isCacheFresh && cached!.trendGranularity) ? cached!.trendGranularity : 'daily');
    const [rangeTotalRevenue, setRangeTotalRevenue] = useState<number | null>(() => isCacheFresh ? (cached!.rangeTotalRevenue ?? null) : null);

    const [loadingKpis, setLoadingKpis] = useState(() => !isCacheFresh && Boolean(activePath && activePath.trim() !== ''));
    const [loadingTrend, setLoadingTrend] = useState(() => !isCacheFresh && Boolean(activePath && activePath.trim() !== ''));
    const [isRefreshing, setIsRefreshing] = useState(false);



    const [errorKpis, setErrorKpis] = useState<string | null>(null);
    const [dbDatasets, setDbDatasets] = useState<DatasetOption[]>([]);
    const [fileDatasets, setFileDatasets] = useState<DatasetOption[]>([]);
    const [isDropdownOpen, setIsDropdownOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const hasRetriedRef = useRef(false);

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsDropdownOpen(false);
            }
        };
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                setIsDropdownOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        document.addEventListener('keydown', handleKeyDown);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
            document.removeEventListener('keydown', handleKeyDown);
            if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
        };
    }, []);

    // Load available datasets and auto-select if none active or current is invalid for the active domain
    useEffect(() => {
        let isMounted = true;
        fetch(`/api/files?domain=${encodeURIComponent(user.domain)}`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!isMounted) return;

                const domainFiles = (data?.files || []).filter((f: any) => {
                    const fDom = (f.domain || '').toLowerCase().trim();
                    return !fDom || fDom === user.domain.toLowerCase().trim();
                });

                const rawDbFiles = domainFiles.filter((f: any) => f.source_type === 'database' || (f.file_path && f.file_path.startsWith('sql://')));
                
                // Extract unique database group names for whole-database selection
                const uniqueDbNames = Array.from(new Set(rawDbFiles.map((f: any) => f.group_name || f.database_name).filter(Boolean))) as string[];

                // Custom Database Groups configured by user
                const customGroups = getCustomDbGroups(user.domain);
                const customGroupEntries = customGroups.map((grp: CustomDbGroup) => {
                    const memberDbs = grp.dbNames;
                    const dbKey = memberDbs.join(',');
                    return {
                        name: `${grp.name} (${memberDbs.join(' + ')})`,
                        path: `db://${dbKey}`,
                        kind: 'whole_db' as const,
                        dbName: dbKey,
                        isCustomGroup: true,
                        customGroupName: grp.name
                    };
                });

                const wholeDbEntries = uniqueDbNames.map((dbName: string) => ({
                    name: `${dbName} (Whole Database)`,
                    path: `db://${dbName}`,
                    kind: 'whole_db' as const,
                    dbName
                }));

                const allDbEntry = (uniqueDbNames.length > 1 && customGroupEntries.length === 0) ? [{
                    name: `All Connected Databases (${uniqueDbNames.length} DBs Consolidated)`,
                    path: 'db://all',
                    kind: 'whole_db' as const,
                    dbName: 'all'
                }] : [];

                const dbList = [...customGroupEntries, ...allDbEntry, ...wholeDbEntries];

                const fileList = domainFiles
                    .filter((f: any) => f.source_type !== 'database' && f.file_path && !f.file_path.startsWith('sql://') && f.file_size_bytes > 0)
                    .map((f: any) => ({
                        name: f.filename,
                        path: f.file_path,
                        kind: 'file' as const
                    }));

                setDbDatasets(dbList);
                setFileDatasets(fileList);

                const allList = [...dbList, ...fileList];

                // Check if user has explicitly chosen a dataset for this domain during their session
                let manualChoice: string | null = null;
                try {
                    manualChoice = sessionStorage.getItem(`llm_konnect_user_manual_dataset_${user.domain}`);
                } catch {}

                const isManualValid = Boolean(manualChoice && allList.some((d: any) => d.path === manualChoice));

                if (isManualValid && manualChoice) {
                    if (activePath !== manualChoice) {
                        setActivePath(manualChoice);
                    }
                } else if (allList.some((d: any) => d.path === activePath)) {
                    // Current active path is already valid for this domain; retain it
                } else if (allList.length > 0) {
                    if (customGroupEntries.length > 0) {
                        setActivePath(customGroupEntries[0].path);
                    } else if (wholeDbEntries.length > 0) {
                        setActivePath(wholeDbEntries[0].path);
                    } else {
                        // Fallback to first available file in this domain
                        setActivePath(allList[0].path);
                    }
                } else {
                    // No datasets in this domain -> cleanly clear activePath and metrics
                    setActivePath('');
                    setKpis(null);
                    setExpiry(null);
                    setTrend(null);
                    setRangeTotalRevenue(null);
                    setLoadingKpis(false);
                    setLoadingTrend(false);
                }
            })
            .catch(() => {
                if (isMounted) {
                    setDbDatasets([]);
                    setFileDatasets([]);
                    setActivePath('');
                }
            });

        return () => { isMounted = false; };
    }, [user.domain]);

    useEffect(() => {
        document.title = `${activeDomainMeta.name} Dashboard — LLM-KONNECT`;
        const activeVer = getDataVersion();
        const currentCached = getCachedEntry(cacheKey);

        if (currentCached && currentCached.dataVersion === activeVer) {
            setKpis(currentCached.kpis);
            setExpiry(currentCached.expiry);
            setTrend(currentCached.trend);
            setRangeTotalRevenue(currentCached.rangeTotalRevenue ?? null);
            if (currentCached.trendRange) setTrendRange(currentCached.trendRange);
            if (currentCached.trendGranularity) setTrendGranularity(currentCached.trendGranularity);
            setKbStats(currentCached.kbStats);
            setLoadingKpis(false);
            setLoadingTrend(false);
            // Persistent session cache matches underlying data; do NOT re-fetch
        } else {
            void fetchAllData(false);
        }
    }, [activePath, user.domain]);

    // Helper to parse responses and log errors
    const handleResponse = async (r: Response, name: string) => {
        if (!r.ok) {
            const text = await r.text().catch(() => '');
            console.error(`[${name}] API Error: ${r.status} ${r.statusText}`, text);
            let detail = `${name} fetch failed (Status ${r.status})`;
            try {
                const parsed = JSON.parse(text);
                if (parsed?.detail) detail = parsed.detail;
            } catch {}
            throw new Error(detail);
        }
        const data = await r.json();
        return data;
    };

    // Dedicated fetch for trend with range preset (7d, 28d, 6m, all) and granularity (daily, monthly)
    const fetchTrendData = async (
        range = trendRange,
        gran = trendGranularity,
        targetPath = activePath
    ) => {
        if (!targetPath || targetPath.trim() === '') {
            setTrend(null);
            setRangeTotalRevenue(null);
            setLoadingTrend(false);
            return;
        }

        setLoadingTrend(true);
        try {
            const trendData = await fetchWithTimeout('/api/analytics/trend', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    file_path: targetPath,
                    domain: user.domain,
                    range_preset: range,
                    granularity: gran,
                    filters: {}
                })
            }).then(r => handleResponse(r, "Trend"));

            const rawSeries = trendData?.trend?.series || trendData?.monthly || trendData?.series || [];
            const fetched = rawSeries.map((p: any) => ({
                period: p.period || p.label || p.date || 'Unknown',
                revenue: Number(p.revenue ?? p.value ?? 0),
                transaction_count: Number(p.transaction_count ?? p.row_count ?? 0),
                moving_average: p.moving_average !== undefined && p.moving_average !== null ? Number(p.moving_average) : undefined
            }));

            const total = trendData?.total_revenue ?? fetched.reduce((acc: number, curr: any) => acc + (curr.revenue || 0), 0);
            setTrend(fetched);
            setRangeTotalRevenue(total);
            setLoadingTrend(false);

            saveCachedEntry(cacheKey, {
                trend: fetched,
                rangeTotalRevenue: total,
                trendRange: range,
                trendGranularity: gran
            });
            return { trend: fetched, total };
        } catch (err) {
            console.error("Failed to fetch trend:", err);
            setTrend([]);
            setRangeTotalRevenue(0);
            setLoadingTrend(false);
            return { trend: [], total: 0 };
        }
    };

    const fetchAllData = async (
        isSilent = false,
        range: '7d' | '28d' | '6m' | 'all' = trendRange,
        gran: 'daily' | 'monthly' = trendGranularity
    ) => {
        setErrorKpis(null);

        // If no file has been connected yet, show clean initial empty state instead of 404 error
        if (!activePath || activePath.trim() === '') {
            setLoadingKpis(false);
            setLoadingTrend(false);
            setKpis(null);
            setExpiry(null);
            setTrend(null);
            setRangeTotalRevenue(null);
            return;
        }

        if (!isSilent) {
            setLoadingKpis(true);
            setLoadingTrend(true);
            setErrorKpis(null);
        }

        const domain = user.domain;

        let fetchedKpis: Record<string, KPIValue> | null = null;
        let fetchedExpiry: Record<string, KPIValue> | null = null;
        let fetchedKbStats: KBStats | null = null;

        // Fetch primary KPIs + Expiry in parallel for efficiency
        try {
            const [kpiData, expiryData] = await Promise.all([
                fetchWithTimeout('/api/analytics/kpis', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        file_path: activePath,
                        domain,
                        range_preset: range,
                        include_validation: true
                    })
                }).then(r => handleResponse(r, "KPIs")),
                domain === 'pharmacy' ? (
                    fetchWithTimeout('/api/analytics/expiry-report', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ file_path: activePath, domain })
                    }).then(r => handleResponse(r, "Expiry"))
                ) : Promise.resolve(null)
            ]);

            fetchedKpis = kpiData?.kpis || kpiData;
            fetchedExpiry = expiryData?.kpis || expiryData;
            setKpis(fetchedKpis);
            setExpiry(fetchedExpiry);
            setLoadingKpis(false);
            hasRetriedRef.current = false;
        } catch (err: any) {
            setErrorKpis(err.message);
            setKpis(null);
            setExpiry(null);
            setLoadingKpis(false);

            // Auto-retry once after 2.5s if backend was still warming up on initial load
            if (!hasRetriedRef.current && activePath) {
                hasRetriedRef.current = true;
                retryTimeoutRef.current = setTimeout(() => {
                    void fetchAllData(false, range, gran);
                }, 2500);
            }
        }

        // Fetch Trend with current range and granularity
        const trendResult = await fetchTrendData(range, gran, activePath);

        // Fetch KB info
        try {
            const kbData = await fetchWithTimeout('/api/kb/stats').then(r => r.ok ? r.json() : null);
            fetchedKbStats = kbData;
            setKbStats(kbData);
        } catch {
            fetchedKbStats = null;
            setKbStats(null);
        }

        // Save to persistent session cache with current dataset version
        saveCachedEntry(cacheKey, {
            kpis: fetchedKpis,
            expiry: fetchedExpiry,
            trend: trendResult?.trend ?? null,
            rangeTotalRevenue: trendResult?.total ?? null,
            trendRange: range,
            trendGranularity: gran,
            kbStats: fetchedKbStats,
            dataVersion: getDataVersion()
        });
    };

    const handleManualRefresh = async () => {
        if (isRefreshing || loadingKpis) return;
        setIsRefreshing(true);
        try {
            await fetchAllData(false);
        } finally {
            setIsRefreshing(false);
        }
    };

    const handleRangeChange = (newRange: '7d' | '28d' | '6m' | 'all') => {
        setTrendRange(newRange);
        const newGran = (newRange === '7d' || newRange === '28d') ? 'daily' : 'monthly';
        setTrendGranularity(newGran);
        void fetchAllData(false, newRange, newGran);
    };


    // Helper to format values clearly
    const formatValue = (val: number, unit?: string) => {
        if (unit === 'PKR') return val.toLocaleString(undefined, { maximumFractionDigits: 0 });
        if (unit === '%') return val.toFixed(1);
        return val.toLocaleString();
    };

    // Renders a single KPI Card
    const renderKPICard = (key: string, title: string, icon: React.ReactNode, fallbackUnit: string, sourceObj?: KPIValue) => {
        const isUnavailable = !sourceObj || sourceObj.status === 'unavailable' || sourceObj.value == null;

        return (
            <div className="kpi-card" key={key}>
                <div className="kpi-header">
                    <div className="kpi-icon-wrap">{icon}</div>
                    <div className="kpi-name">{title}</div>
                </div>

                <div className="kpi-value-wrap">
                    {isUnavailable ? (
                        <>
                            <div className="kpi-value unavailable"><Minus size={36} strokeWidth={3} /></div>
                        </>
                    ) : (
                        <>
                            <div className="kpi-value">{formatValue(sourceObj!.value!, sourceObj!.unit || fallbackUnit)}</div>
                            <div className={`kpi-unit ${sourceObj!.unit === 'count' ? 'count' : ''}`}>
                                {sourceObj!.unit || fallbackUnit}
                            </div>
                        </>
                    )}
                </div>

                {isUnavailable && (
                    <div className="kpi-unavailable-text" style={{ fontSize: '0.76rem', color: '#94a3b8' }}>
                        {sourceObj?.reason ? 'Cost column not in table' : 'Data not connected'}
                    </div>
                )}
            </div>
        );
    };

    const allDatasets = [...dbDatasets, ...fileDatasets];
    const activeItem = allDatasets.find((d) => d.path === activePath);
    const activeDisplayLabel = activeItem
        ? (activeItem.customGroupName || activeItem.name)
        : (activePath ? activePath.split('/').pop()?.replace(/^sql:\/\//, '') : 'Select Dataset');

    return (
        <div className="dashboard-container">
            {/* Header */}
            <div className="dashboard-header">
                <div className="dashboard-title">
                    <h1>Executive Overview</h1>
                    <p>{user.organization ? `${user.organization} · ` : ''}Real-time business performance analytics</p>
                </div>
                <div className="dashboard-meta" style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                    {(dbDatasets.length > 0 || fileDatasets.length > 0) && (
                        <div className="google-dataset-select-wrap" ref={dropdownRef}>
                            <button
                                type="button"
                                className={`google-dataset-trigger ${isDropdownOpen ? 'open' : ''}`}
                                onClick={() => setIsDropdownOpen(!isDropdownOpen)}
                                aria-haspopup="listbox"
                                aria-expanded={isDropdownOpen}
                            >
                                {activeItem?.isCustomGroup ? (
                                    <Boxes size={15} className="google-dataset-icon" />
                                ) : activeItem?.kind === 'file' ? (
                                    <FileSpreadsheet size={15} className="google-dataset-icon" />
                                ) : activeItem?.kind === 'table' ? (
                                    <Table2 size={15} className="google-dataset-icon" />
                                ) : (
                                    <Database size={15} className="google-dataset-icon" />
                                )}
                                <span className="google-dataset-label">{activeDisplayLabel}</span>
                                <ChevronDown size={14} className={`google-dataset-chevron ${isDropdownOpen ? 'rotated' : ''}`} />
                            </button>

                            {isDropdownOpen && (
                                <div className="google-dataset-menu" role="listbox">
                                    {dbDatasets.length > 0 && (
                                        <>
                                            <div className="google-dataset-section-header">
                                                <Database size={12} />
                                                <span>Connected Databases (SQL)</span>
                                            </div>
                                            {dbDatasets.map((ds) => {
                                                const isSelected = activePath === ds.path;
                                                const isTable = ds.kind === 'table';
                                                const isCustom = ds.isCustomGroup;
                                                return (
                                                    <button
                                                        key={ds.path}
                                                        type="button"
                                                        role="option"
                                                        aria-selected={isSelected}
                                                        className={`google-dataset-item ${isTable ? 'is-table' : ''} ${isSelected ? 'active' : ''}`}
                                                        onClick={() => {
                                                            try {
                                                                sessionStorage.setItem('llm_konnect_user_manual_dataset_choice', ds.path);
                                                                sessionStorage.setItem(`llm_konnect_user_manual_dataset_${user.domain}`, ds.path);
                                                            } catch {}
                                                            setActivePath(ds.path);
                                                            setIsDropdownOpen(false);
                                                        }}
                                                    >
                                                        {isCustom ? (
                                                            <Boxes size={14} style={{ color: 'var(--brand-green)', flexShrink: 0 }} />
                                                        ) : isTable ? (
                                                            <Table2 size={13} style={{ opacity: 0.65, flexShrink: 0 }} />
                                                        ) : (
                                                            <Database size={14} style={{ color: 'var(--brand-green)', flexShrink: 0 }} />
                                                        )}
                                                        <span className="google-dataset-item-text">{ds.name}</span>
                                                        {isSelected && <Check size={14} className="google-dataset-item-check" />}
                                                    </button>
                                                );
                                            })}
                                        </>
                                    )}

                                    {fileDatasets.length > 0 && (
                                        <>
                                            <div className="google-dataset-section-header">
                                                <FileSpreadsheet size={12} />
                                                <span>Uploaded Files & Spreadsheets</span>
                                            </div>
                                            {fileDatasets.map((ds) => {
                                                const isSelected = activePath === ds.path;
                                                return (
                                                    <button
                                                        key={ds.path}
                                                        type="button"
                                                        role="option"
                                                        aria-selected={isSelected}
                                                        className={`google-dataset-item ${isSelected ? 'active' : ''}`}
                                                        onClick={() => {
                                                            try {
                                                                sessionStorage.setItem('llm_konnect_user_manual_dataset_choice', ds.path);
                                                            } catch {}
                                                            setActivePath(ds.path);
                                                            setIsDropdownOpen(false);
                                                        }}
                                                    >
                                                        <FileSpreadsheet size={14} style={{ opacity: 0.75, flexShrink: 0 }} />
                                                        <span className="google-dataset-item-text">{ds.name}</span>
                                                        {isSelected && <Check size={14} className="google-dataset-item-check" />}
                                                    </button>
                                                );
                                            })}
                                        </>
                                    )}
                                </div>
                            )}
                        </div>
                    )}
                    <button
                        type="button"
                        className="google-refresh-btn"
                        onClick={handleManualRefresh}
                        disabled={isRefreshing || loadingKpis}
                        title="Refresh metrics manually"
                    >
                        <RefreshCw size={13} className={isRefreshing ? 'spin-icon' : ''} />
                        <span>{isRefreshing ? 'Refreshing...' : 'Refresh'}</span>
                    </button>
                </div>
            </div>

            {/* No Data Banner when no file is active */}
            {(!activePath || errorKpis) && (
                <div
                    style={{
                        background: 'rgba(16, 185, 129, 0.08)',
                        border: '1px solid rgba(16, 185, 129, 0.25)',
                        borderRadius: '12px',
                        padding: '1.25rem 1.5rem',
                        marginBottom: '1.5rem',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        flexWrap: 'wrap',
                        gap: '1rem'
                    }}
                >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.85rem' }}>
                        <div style={{ background: 'rgba(16, 185, 129, 0.2)', padding: '0.6rem', borderRadius: '10px', color: '#10b981', display: 'flex' }}>
                            <Database size={22} />
                        </div>
                        <div>
                            <div style={{ fontWeight: 700, color: '#ffffff', fontSize: '0.98rem' }}>
                                Connect your {activeDomainMeta.name} data
                            </div>
                            <div style={{ color: '#9ca3af', fontSize: '0.84rem', marginTop: '0.2rem' }}>
                                {activePath ? errorKpis : `Upload a CSV/Excel file or connect a database to populate live analytics and KPIs for ${activeDomainMeta.name}.`}
                            </div>
                        </div>
                    </div>
                    <Link
                        to="/connect"
                        style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '0.5rem',
                            background: '#10b981',
                            color: '#0f172a',
                            fontWeight: 700,
                            padding: '0.6rem 1.15rem',
                            borderRadius: '8px',
                            textDecoration: 'none',
                            fontSize: '0.86rem',
                            boxShadow: '0 4px 12px rgba(16, 185, 129, 0.25)'
                        }}
                    >
                        Connect Data Source <ArrowRight size={15} />
                    </Link>
                </div>
            )}

            {/* Period Selection & Summary Bar for Calculated Metrics */}
            <div className="kpi-filter-bar">
                <div className="kpi-filter-label">
                    <Calendar size={14} style={{ color: 'var(--brand-green)' }} />
                    <span>Timeframe</span>
                </div>
                <div className="range-preset-group">
                    <button
                        type="button"
                        className={`range-pill ${trendRange === '7d' ? 'active' : ''}`}
                        onClick={() => handleRangeChange('7d')}
                    >
                        Last 7 Days
                    </button>
                    <button
                        type="button"
                        className={`range-pill ${trendRange === '28d' ? 'active' : ''}`}
                        onClick={() => handleRangeChange('28d')}
                    >
                        Last 28 Days
                    </button>
                    <button
                        type="button"
                        className={`range-pill ${trendRange === '6m' ? 'active' : ''}`}
                        onClick={() => handleRangeChange('6m')}
                    >
                        Last 6 Months
                    </button>
                    <button
                        type="button"
                        className={`range-pill ${trendRange === 'all' ? 'active' : ''}`}
                        onClick={() => handleRangeChange('all')}
                    >
                        All Time
                    </button>
                </div>
            </div>

            {/* KPIs Grid */}
            {loadingKpis ? (
                <div className="kpi-grid">
                    {[1, 2, 3, 4, 5, 6].map(i => <KPISkeleton key={i} />)}
                </div>
            ) : user.domain === 'ecommerce' ? (
                <div className="kpi-grid">
                    {renderKPICard("gmv", "Gross Merchandise Value (GMV)", <DollarSign size={18} />, "PKR", kpis?.['gmv'] || kpis?.['total_revenue'])}
                    {renderKPICard("net_sales", "Net Sales", <DollarSign size={18} />, "PKR", kpis?.['ecommerce_net_sales'] || kpis?.['total_revenue'])}
                    {renderKPICard("gp", "Gross Profit", <TrendingUp size={18} />, "PKR", kpis?.['ecom_gross_profit'] || kpis?.['gross_profit'])}
                    {renderKPICard("gm", "Gross Margin", <BarChart2 size={18} />, "%", kpis?.['ecom_gross_margin_pct'] || kpis?.['gross_margin_pct'] || kpis?.['gross_margin'])}
                    {renderKPICard("tx", "Total Orders", <Receipt size={18} />, "count", kpis?.['transaction_count'] || kpis?.['total_orders'])}
                    {renderKPICard("atv", "Avg Order Value (AOV)", <CreditCard size={18} />, "PKR", kpis?.['average_order_value_ecom'] || kpis?.['average_transaction_value'] || kpis?.['avg_transaction_value'])}
                    {renderKPICard("refund_rate", "Refund Rate", <AlertTriangle size={18} />, "%", kpis?.['refund_rate_pct_ecom'] || kpis?.['refund_rate_pct'])}
                    {renderKPICard("repeat_rate", "Repeat Customer Rate", <ShoppingBag size={18} />, "%", kpis?.['repeat_customer_rate_pct'])}
                    {renderKPICard("skus", "Active SKUs / Products", <ShoppingBag size={18} />, "count", kpis?.['active_skus'] || kpis?.['total_products'] || kpis?.['unique_customers_count'])}
                </div>
            ) : user.domain === 'home_finance' ? (
                <div className="kpi-grid">
                    {renderKPICard("rev", "Total Inflow / Income", <DollarSign size={18} />, "PKR", kpis?.['total_income'] || kpis?.['total_revenue'])}
                    {renderKPICard("gp", "Net Savings", <TrendingUp size={18} />, "PKR", kpis?.['net_savings'] || kpis?.['gross_profit'])}
                    {renderKPICard("gm", "Savings Rate", <BarChart2 size={18} />, "%", kpis?.['savings_rate'] || kpis?.['gross_margin_pct'])}
                    {renderKPICard("tx", "Transactions", <Receipt size={18} />, "count", kpis?.['transaction_count'])}
                    {renderKPICard("expenses", "Monthly Expenses", <AlertTriangle size={18} />, "PKR", kpis?.['total_expenses'])}
                </div>
            ) : user.domain === 'finance' ? (
                <div className="kpi-grid">
                    {renderKPICard("rev", "Total Revenue", <DollarSign size={18} />, "PKR", kpis?.['total_revenue'])}
                    {renderKPICard("gp", "Gross Profit", <TrendingUp size={18} />, "PKR", kpis?.['gross_profit'])}
                    {renderKPICard("gm", "Gross Margin", <BarChart2 size={18} />, "%", kpis?.['gross_margin_pct'] || kpis?.['gross_margin'])}
                    {renderKPICard("tx", "Total Invoices", <Receipt size={18} />, "count", kpis?.['transaction_count'])}
                    {renderKPICard("ebitda", "Operating Balance", <Briefcase size={18} />, "PKR", kpis?.['operating_balance'] || kpis?.['ebitda'])}
                </div>
            ) : (
                <div className="kpi-grid">
                    {renderKPICard("rev", "Total Revenue", <DollarSign size={18} />, "PKR", kpis?.['total_revenue'])}
                    {renderKPICard("gp", "Gross Profit", <TrendingUp size={18} />, "PKR", kpis?.['gross_profit'])}
                    {renderKPICard("gm", "Gross Margin", <BarChart2 size={18} />, "%", kpis?.['gross_margin_pct'] || kpis?.['gross_margin'])}
                    {renderKPICard("tx", "Total Transactions", <Receipt size={18} />, "count", kpis?.['transaction_count'])}
                    {renderKPICard("atv", "Avg Transaction Value", <CreditCard size={18} />, "PKR", kpis?.['average_transaction_value'] || kpis?.['avg_transaction_value'])}
                    {renderKPICard("exp", "Near-Expiry Items", <AlertTriangle size={18} />, "count", expiry?.['near_expiry_item_count'])}
                </div>
            )}

            {/* Charts */}
            <div className="chart-section">
                <div className="chart-header-row">
                    <div className="chart-header-left">
                        <div className="chart-title-group">
                            <h3>Revenue Trend</h3>
                            {rangeTotalRevenue !== null && (
                                <span className="chart-range-badge" title="Total revenue for all data points in the selected range">
                                    Total in Selected Period: <strong>PKR {Math.round(rangeTotalRevenue).toLocaleString()}</strong>
                                </span>
                            )}
                        </div>
                        <p className="chart-subtitle">
                            {trendGranularity === 'daily' ? 'Daily Revenue Breakdown' : 'Monthly Aggregated Revenue'} · Deterministic from source data
                        </p>
                    </div>

                    <div className="chart-header-controls">
                        {/* Range presets */}
                        <div className="range-preset-group">
                            <button
                                type="button"
                                className={`range-pill ${trendRange === '7d' ? 'active' : ''}`}
                                onClick={() => handleRangeChange('7d')}
                            >
                                Last 7 Days
                            </button>
                            <button
                                type="button"
                                className={`range-pill ${trendRange === '28d' ? 'active' : ''}`}
                                onClick={() => handleRangeChange('28d')}
                            >
                                Last 28 Days
                            </button>
                            <button
                                type="button"
                                className={`range-pill ${trendRange === '6m' ? 'active' : ''}`}
                                onClick={() => handleRangeChange('6m')}
                            >
                                Last 6 Months
                            </button>
                            <button
                                type="button"
                                className={`range-pill ${trendRange === 'all' ? 'active' : ''}`}
                                onClick={() => handleRangeChange('all')}
                            >
                                All Time
                            </button>
                        </div>

                        {/* Granularity Toggle */}
                        <div className="granularity-toggle-group">
                            <button
                                type="button"
                                className={`gran-toggle-btn ${trendGranularity === 'daily' ? 'active' : ''}`}
                                onClick={() => {
                                    setTrendGranularity('daily');
                                    fetchTrendData(trendRange, 'daily');
                                }}
                            >
                                Daily
                            </button>
                            <button
                                type="button"
                                className={`gran-toggle-btn ${trendGranularity === 'monthly' ? 'active' : ''}`}
                                onClick={() => {
                                    setTrendGranularity('monthly');
                                    fetchTrendData(trendRange, 'monthly');
                                }}
                            >
                                Monthly
                            </button>
                        </div>
                    </div>
                </div>

                {loadingTrend ? (
                    <ChartSkeleton />
                ) : !trend || trend.length === 0 ? (
                    <div className="empty-chart">
                        <Activity size={32} />
                        <p>No dataset connected yet. Connect a data source to generate trends.</p>
                    </div>
                ) : (
                    <div className="chart-container">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={trend} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                                <defs>
                                    <linearGradient id="colorRev" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#10b981" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#374151" />
                                <XAxis dataKey="period" stroke="#9ca3af" tickLine={false} />
                                <YAxis stroke="#9ca3af" tickLine={false} tickFormatter={(v) => Math.abs(v) >= 1000000 ? `${(v / 1000000).toFixed(1)}M` : Math.abs(v) >= 1000 ? `${(v / 1000).toFixed(0)}k` : `${v}`} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#1f2937', borderColor: '#374151', borderRadius: '8px', color: '#fff', fontSize: '0.85rem' }}
                                    formatter={(v: any, _name: any, item: any) => {
                                        const txCount = item?.payload?.transaction_count;
                                        const label = trendGranularity === 'daily' ? "Day's Revenue" : "Month's Revenue";
                                        return [
                                            `PKR ${Number(v).toLocaleString()}${txCount ? ` (${txCount} txs)` : ''}`,
                                            label
                                        ];
                                    }}
                                    labelFormatter={(label: any) => {
                                        return trendGranularity === 'daily' ? `Date: ${label}` : `Period: ${label}`;
                                    }}
                                />

                                <Area type="monotone" dataKey="revenue" stroke="#10b981" strokeWidth={2} fillOpacity={1} fill="url(#colorRev)" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* Knowledge Base Status Bar */}
            {kbStats && (
                <div className="kb-stats-bar">
                    <div className="kb-stats-info">
                        <Database size={16} />
                        <span>Connected to Vector Knowledge Base: <strong>{kbStats.collection_name}</strong> ({kbStats.total_chunks} indexed chunks)</span>
                    </div>
                </div>
            )}
        </div>
    );
}
