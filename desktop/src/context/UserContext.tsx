import React, { createContext, useContext, useState, useEffect } from 'react';

export type DomainType = 'pharmacy' | 'ecommerce' | 'finance' | 'home_finance';

export interface DomainMeta {
    id: DomainType;
    name: string;
    icon: string;
    description: string;
    color: string;
    exampleFiles: string[];
    suggestedQueries: string[];
}

export const DOMAIN_METAS: Record<DomainType, DomainMeta> = {
    pharmacy: {
        id: 'pharmacy',
        name: 'Pharmacy & Health',
        icon: '💊',
        description: 'Medicine stocks, batches, expiries, suppliers & prescriptions.',
        color: '#10b981',
        exampleFiles: ['test_pharmacy_small.csv', 'inventory_batches.xlsx'],
        suggestedQueries: [
            'Which medicines are expiring in the next 30 days?',
            'What is our total revenue from prescriptions this month?',
            'Show items with zero or critically low stock.'
        ]
    },
    ecommerce: {
        id: 'ecommerce',
        name: 'E-Commerce & Retail',
        icon: '🛒',
        description: 'Orders, product SKUs, customers, fulfillment & discounts.',
        color: '#06b6d4',
        exampleFiles: ['orders_2024.csv', 'products_catalog.xlsx'],
        suggestedQueries: [
            'What is our top selling product category by revenue?',
            'What is the average order value and return rate?',
            'Show recent orders with pending fulfillment status.'
        ]
    },
    finance: {
        id: 'finance',
        name: 'Business Finance',
        icon: '💼',
        description: 'P&L ledgers, accounts, bank statements, vendors & invoices.',
        color: '#8b5cf6',
        exampleFiles: ['pl_ledger_q1.csv', 'vendor_invoices.xlsx'],
        suggestedQueries: [
            'What is our net profit margin for the current period?',
            'List the top 5 vendor payables currently outstanding.',
            'Summarize total operating expenses vs gross revenue.'
        ]
    },
    home_finance: {
        id: 'home_finance',
        name: 'Home & Personal Finance',
        icon: '🏠',
        description: 'Personal budgets, income, expenses, savings & bills.',
        color: '#f59e0b',
        exampleFiles: ['personal_budget.csv', 'monthly_expenses.xlsx'],
        suggestedQueries: [
            'How much did we spend on utilities and groceries this month?',
            'What is our current monthly savings rate?',
            'Are any recurring bills due in the next 7 days?'
        ]
    }
};

export interface UserProfile {
    accountName: string;
    organization: string;
    email: string;
    role: string;
    domain: DomainType;
    isSetupComplete: boolean;
}

interface UserContextType {
    user: UserProfile;
    setDomain: (domain: DomainType) => void;
    updateProfile: (profile: Partial<UserProfile>) => void;
    completeOnboarding: (data: { accountName: string; organization: string; email: string; domain: DomainType }) => void;
    resetProfile: () => void;
    isSettingsOpen: boolean;
    openSettings: () => void;
    closeSettings: () => void;
    activeDomainMeta: DomainMeta;
}

const STORAGE_KEY = 'llm_konnect_user_profile';

const DEFAULT_PROFILE: UserProfile = {
    accountName: '',
    organization: '',
    email: '',
    role: 'Admin',
    domain: 'pharmacy',
    isSetupComplete: false
};

const UserContext = createContext<UserContextType | null>(null);

export function UserProvider({ children }: { children: React.ReactNode }) {
    const [user, setUser] = useState<UserProfile>(() => {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            if (saved) {
                const parsed = JSON.parse(saved);
                return { ...DEFAULT_PROFILE, ...parsed };
            }
        } catch (e) {
            console.error('Error loading user profile from storage', e);
        }
        return DEFAULT_PROFILE;
    });

    const [isSettingsOpen, setIsSettingsOpen] = useState(false);

    useEffect(() => {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
        } catch (e) {
            console.error('Error persisting user profile', e);
        }
    }, [user]);

    const setDomain = (domain: DomainType) => {
        setUser(prev => ({ ...prev, domain }));
    };

    const updateProfile = (fields: Partial<UserProfile>) => {
        setUser(prev => ({ ...prev, ...fields }));
    };

    const completeOnboarding = (data: { accountName: string; organization: string; email: string; domain: DomainType }) => {
        setUser({
            accountName: data.accountName || 'Admin User',
            organization: data.organization || 'My Workspace',
            email: data.email || 'admin@llm-konnect.local',
            role: 'Administrator',
            domain: data.domain,
            isSetupComplete: true
        });
    };

    const resetProfile = () => {
        setUser(DEFAULT_PROFILE);
    };

    const activeDomainMeta = DOMAIN_METAS[user.domain] || DOMAIN_METAS.pharmacy;

    return (
        <UserContext.Provider
            value={{
                user,
                setDomain,
                updateProfile,
                completeOnboarding,
                resetProfile,
                isSettingsOpen,
                openSettings: () => setIsSettingsOpen(true),
                closeSettings: () => setIsSettingsOpen(false),
                activeDomainMeta
            }}
        >
            {children}
        </UserContext.Provider>
    );
}

export function useUser() {
    const ctx = useContext(UserContext);
    if (!ctx) {
        throw new Error('useUser must be used within a UserProvider');
    }
    return ctx;
}
