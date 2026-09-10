import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

interface FileContextType {
    activePath: string;
    setActivePath: (path: string) => void;
}

const STORAGE_KEY = 'llm_konnect_active_file';

const FileContext = createContext<FileContextType>({
    activePath: '',
    setActivePath: () => { },
});

export function FileProvider({ children }: { children: ReactNode }) {
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

    return (
        <FileContext.Provider value={{ activePath, setActivePath }}>
            {children}
        </FileContext.Provider>
    );
}

export function useFilePath() {
    return useContext(FileContext);
}
