import { createContext, useContext, useState } from 'react';
import type { ReactNode } from 'react';

interface FileContextType {
    activePath: string;
    setActivePath: (path: string) => void;
}

const DEFAULT_PATH = "C:/Users/Administrator/Desktop/llm-Konnect/data/uploads/test_pharmacy_small.csv";

const FileContext = createContext<FileContextType>({
    activePath: DEFAULT_PATH,
    setActivePath: () => { },
});

export function FileProvider({ children }: { children: ReactNode }) {
    const [activePath, setActivePath] = useState(DEFAULT_PATH);
    return (
        <FileContext.Provider value={{ activePath, setActivePath }}>
            {children}
        </FileContext.Provider>
    );
}

export function useFilePath() {
    return useContext(FileContext);
}
