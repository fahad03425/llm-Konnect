import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import {
    DollarSign, TrendingUp, BarChart2, Receipt, CreditCard, AlertTriangle,
    Database, Activity, Minus, ShoppingBag, Briefcase, ArrowRight
} from 'lucide-react';
import {
    AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { useFilePath } from '../context/FileContext';
import { useUser } from '../context/UserContext';
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
    const { user, activeDomainMeta } = useUser();
    const [kpis, setKpis] = useState<Record<string, KPIValue> | null>(null);
    const [expiry, setExpiry] = useState<Record<string, KPIValue> | null>(null);
    const [trend, setTrend] = useState<TrendDataPoint[] | null>(null);
    const [kbStats, setKbStats] = useState<KBStats | null>(null);

    const [loadingKpis, setLoadingKpis] = useState(true);
    const [loadingTrend, setLoadingTrend] = useState(true);

    const [errorKpis, setErrorKpis] = useState<string | null>(null);

    useEffect(() => {
        document.title = `${activeDomainMeta.name} Dashboard — LLM-KONNECT`;
        void fetchAllData();
    }, [activePath, user.domain]);

    const fetchAllData = async () => {
        setErrorKpis(null);

        // If no file has been connected yet, show clean initial empty state instead of 404 error
        if (!activePath || activePath.trim() === '') {
            setLoadingKpis(false);
            setLoadingTrend(false);
            setKpis(null);
            setExpiry(null);
            setTrend(null);
            return;
        }

        setLoadingKpis(true);
        setLoadingTrend(true);

        // Helper to parse responses and log errors
        const handleResponse = async (r: Response, name: string) => {
            if (!r.ok) {
                const text = await r.text().catch(() => '');
                if (r.status === 404) {
                    throw new Error("Data file not found. Please connect a valid dataset in Connect Source.");
                }
                console.error(`[${name}] API Error: ${r.status} ${r.statusText}`, text);
                throw new Error(`${name} fetch failed (Status ${r.status})`);
            }
            const data = await r.json();
            return data;
        };

        const domain = user.domain;

        // Fetch primary KPIs + Expiry in parallel for efficiency
        Promise.all([
            fetchWithTimeout('/api/analytics/kpis', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ file_path: activePath, domain, include_validation: true })
            }).then(r => handleResponse(r, "KPIs")),
            domain === 'pharmacy' ? (
                fetchWithTimeout('/api/analytics/expiry-report', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ file_path: activePath, domain })
                }).then(r => handleResponse(r, "Expiry"))
            ) : Promise.resolve(null)
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
            body: JSON.stringify({ file_path: activePath, domain, filters: {} })
        }).then(r => handleResponse(r, "Trend"))
            .then(data => {
                setTrend(data.monthly || []);
                setLoadingTrend(false);
            })
            .catch(() => {
                setTrend([]);
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
                    <div className="kpi-unavailable-text">Data not connected</div>
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
                    <h1>{activeDomainMeta.name} Overview</h1>
                    <p>{user.organization ? `${user.organization} · ` : ''}Executive Analytics Dashboard</p>
                </div>
                <div className="dashboard-meta">
                    <div className="status-badge">
                        <div className="status-dot"></div>
                        Ollama Engine: RUNNING
                    </div>
                    <div className="date-display">{todayStr}</div>
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

            {/* KPIs Grid */}
            {loadingKpis ? (
                <div className="kpi-grid">
                    {[1, 2, 3, 4, 5, 6].map(i => <KPISkeleton key={i} />)}
                </div>
            ) : (
                <div className="kpi-grid">
                    {renderKPICard("rev", user.domain === 'home_finance' ? "Total Inflow / Income" : "Total Revenue", <DollarSign size={18} />, "PKR", kpis?.['total_revenue'] || kpis?.['total_income'])}
                    {renderKPICard("gp", user.domain === 'home_finance' ? "Net Savings" : "Gross Profit", <TrendingUp size={18} />, "PKR", kpis?.['gross_profit'] || kpis?.['net_savings'])}
                    {renderKPICard("gm", user.domain === 'home_finance' ? "Savings Rate" : "Gross Margin", <BarChart2 size={18} />, "%", kpis?.['gross_margin'] || kpis?.['savings_rate'])}
                    {renderKPICard("tx", user.domain === 'ecommerce' ? "Total Orders" : "Total Transactions", <Receipt size={18} />, "count", kpis?.['transaction_count'] || kpis?.['total_orders'])}
                    {renderKPICard("atv", user.domain === 'ecommerce' ? "Avg Order Value (AOV)" : "Avg Transaction Value", <CreditCard size={18} />, "PKR", kpis?.['avg_transaction_value'] || kpis?.['avg_order_value'])}
                    {user.domain === 'pharmacy' && renderKPICard("exp", "Near-Expiry Items", <AlertTriangle size={18} />, "count", expiry?.['near_expiry_item_count'])}
                    {user.domain === 'ecommerce' && renderKPICard("skus", "Active SKUs / Products", <ShoppingBag size={18} />, "count", kpis?.['active_skus'] || kpis?.['total_products'])}
                    {user.domain === 'finance' && renderKPICard("ebitda", "Operating Balance", <Briefcase size={18} />, "PKR", kpis?.['operating_balance'] || kpis?.['ebitda'])}
                    {user.domain === 'home_finance' && renderKPICard("expenses", "Monthly Expenses", <AlertTriangle size={18} />, "PKR", kpis?.['total_expenses'])}
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
                                <YAxis stroke="#9ca3af" tickLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: '#1f2937', borderColor: '#374151', borderRadius: '8px', color: '#fff' }}
                                    formatter={(v: any) => [`PKR ${Number(v).toLocaleString()}`, 'Revenue']}
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
