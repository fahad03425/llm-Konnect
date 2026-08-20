import { useState, useEffect } from 'react';
import {
    DollarSign, TrendingUp, BarChart2, Receipt, CreditCard, AlertTriangle,
    Database, Activity, Minus
} from 'lucide-react';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { useFilePath } from '../context/FileContext';
import './Dashboard.css';

// ----------------------------------------------------------------------
// Types based on the User Specifications
// ----------------------------------------------------------------------

interface KPIValue {
    name?: string;
    value?: number;
    unit?: string;
    status: 'ok' | 'unavailable';
    formula?: string;
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
// Helper Components
// ----------------------------------------------------------------------

const KPISkeleton = () => <div className="skeleton skeleton-kpi" />;
const ChartSkeleton = () => <div className="skeleton skeleton-chart" />;

// ----------------------------------------------------------------------
// Main Dashboard
// ----------------------------------------------------------------------

export default function Dashboard() {
    const { activePath } = useFilePath();
    const [kpis, setKpis] = useState<Record<string, KPIValue> | null>(null);
    const [expiry, setExpiry] = useState<Record<string, KPIValue> | null>(null);
    const [trend, setTrend] = useState<TrendDataPoint[] | null>(null);
    const [kbStats, setKbStats] = useState<KBStats | null>(null);

    const [loadingKpis, setLoadingKpis] = useState(true);
    const [loadingTrend, setLoadingTrend] = useState(true);

    const [errorKpis, setErrorKpis] = useState<string | null>(null);
    const [errorTrend, setErrorTrend] = useState<string | null>(null);

    useEffect(() => {
        document.title = 'Dashboard — LLM-KONNECT';
        void fetchAllData();
    }, [activePath]);

    const fetchAllData = async () => {
        setLoadingKpis(true);
        setLoadingTrend(true);
        setErrorKpis(null);
        setErrorTrend(null);

        // Helper to parse responses and log errors
        const handleResponse = async (r: Response, name: string) => {
            if (!r.ok) {
                const text = await r.text().catch(() => '');
                console.error(`[${name}] API Error: ${r.status} ${r.statusText}`, text);
                throw new Error(`${name} fetch failed (Status ${r.status})`);
            }
            const data = await r.json();
            console.log(`[${name}] Raw Response:`, data);
            return data;
        };

        // Fetch primary KPIs + Expiry in parallel for efficiency
        Promise.all([
            fetchWithTimeout('/api/analytics/kpis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: activePath, domain: "pharmacy", include_validation: true })
            }).then(r => handleResponse(r, "KPIs")),
            fetchWithTimeout('/api/analytics/expiry-report', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: activePath, domain: "pharmacy" })
            }).then(r => handleResponse(r, "Expiry"))
        ]).then(([kpiData, expiryData]) => {
            setKpis(kpiData?.kpis || kpiData);
            setExpiry(expiryData?.kpis || expiryData);
            setLoadingKpis(false);
        }).catch(err => {
            setErrorKpis(err.message);
            setLoadingKpis(false);
        });

        // Fetch Trend separately
        fetchWithTimeout('/api/analytics/trend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ file_path: activePath, domain: "pharmacy", filters: {} })
        }).then(r => handleResponse(r, "Trend"))
            .then(data => {
                setTrend(data.monthly || []);
                setLoadingTrend(false);
            })
            .catch(err => {
                setErrorTrend(err.message);
                setLoadingTrend(false);
            });

        // Fetch KB info
        fetchWithTimeout('/api/kb/stats')
            .then(r => r.ok ? r.json() : null)
            .then(data => setKbStats(data))
            .catch(() => setKbStats(null));
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

                {isUnavailable ? (
                    <div className="kpi-unavailable-text">Data unavailable</div>
                ) : (
                    <div className="kpi-footer">Computed by code · not AI</div>
                )}
            </div>
        );
    };

    // Calculate today's date properly
    const todayStr = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });

    return (
        <div className="dashboard-container">
            {/* Header */}
            <div className="dashboard-header">
                <div className="dashboard-title">
                    <h1>Financial Overview</h1>
                    <p>Pharmacy Analytics Dashboard</p>
                </div>
                <div className="dashboard-meta">
                    <div className="status-badge">
                        <div className="status-dot"></div>
                        Ollama Engine: RUNNING
                    </div>
                    <div className="date-display">{todayStr}</div>
                </div>
            </div>

            {/* KPIs Grid */}
            {loadingKpis ? (
                <div className="kpi-grid">
                    {[1, 2, 3, 4, 5, 6].map(i => <KPISkeleton key={i} />)}
                </div>
            ) : errorKpis ? (
                <div className="error-card">
                    <AlertTriangle size={24} />
                    <strong>Failed to load KPIs</strong>
                    <p>{errorKpis}</p>
                </div>
            ) : (
                <div className="kpi-grid">
                    {renderKPICard("rev", "Total Revenue", <DollarSign size={18} />, "PKR", kpis?.['total_revenue'])}
                    {renderKPICard("gp", "Gross Profit", <TrendingUp size={18} />, "PKR", kpis?.['gross_profit'])}
                    {renderKPICard("gm", "Gross Margin", <BarChart2 size={18} />, "%", kpis?.['gross_margin'])}
                    {renderKPICard("tx", "Total Transactions", <Receipt size={18} />, "count", kpis?.['transaction_count'])}
                    {renderKPICard("atv", "Avg Transaction Value", <CreditCard size={18} />, "PKR", kpis?.['avg_transaction_value'])}
                    {renderKPICard("exp", "Near-Expiry Items", <AlertTriangle size={18} />, "count", expiry?.['near_expiry_item_count'])}
                </div>
            )}

            {/* Charts */}
            <div className="chart-section">
                <div className="chart-header">
                    <h3>Revenue Trend</h3>
                    <p>Deterministic · Computed from source data</p>
                </div>

                {loadingTrend ? (
                    <ChartSkeleton />
                ) : errorTrend ? (
                    <div className="error-card" style={{ height: 350 }}>
                        <AlertTriangle size={24} />
                        <strong>Failed to load trend data</strong>
                        <p>{errorTrend}</p>
                    </div>
                ) : !trend || trend.length === 0 ? (
                    <div className="empty-chart">
                        <Activity size={32} />
                        <p>No trend data available yet.</p>
                    </div>
                ) : (
                    <div className="chart-container">
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={trend} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                                <defs>
                                    <linearGradient id="colorRevenue" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="#0D7377" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="#0D7377" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
                                <XAxis
                                    dataKey="period"
                                    tickLine={false}
                                    axisLine={false}
                                    tick={{ fill: '#6B7280', fontSize: 12 }}
                                    dy={10}
                                />
                                <YAxis
                                    tickFormatter={(val) => `Rs. ${(val / 1000).toFixed(0)}k`}
                                    tickLine={false}
                                    axisLine={false}
                                    tick={{ fill: '#6B7280', fontSize: 12 }}
                                    width={80}
                                />
                                <Tooltip
                                    formatter={(value: any) => [`PKR ${(value || 0).toLocaleString()}`, 'Revenue']}
                                    contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0,0,0,0.1)' }}
                                />
                                <Area
                                    type="monotone"
                                    dataKey="revenue"
                                    stroke="#0D7377"
                                    strokeWidth={3}
                                    fillOpacity={1}
                                    fill="url(#colorRevenue)"
                                />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                )}
            </div>

            {/* Bottom Status Bar */}
            <div className="bottom-status-bar">
                <Database size={16} className="status-icon" />
                Knowledge Base: {kbStats ? kbStats.total_chunks.toLocaleString() : 'N/A'} chunks · {kbStats ? kbStats.collection_name : 'No Collection'} ·
                <span style={{ color: '#0D7377', fontWeight: 600, marginLeft: '0.25rem' }}>Analyzing: {activePath.split(/[/\\]/).pop()}</span>
                <span style={{ color: '#9CA3AF', fontStyle: 'italic', marginLeft: 'auto' }}>All computations are deterministic and traceable to source rows.</span>
            </div>
        </div>
    );
}
