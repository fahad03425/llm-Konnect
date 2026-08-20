import React from 'react';
import { Send, Lock, ShieldCheck, Zap } from 'lucide-react';

interface ComposerProps {
    input: string;
    setInput: (val: string) => void;
    handleSend: (text?: string) => void;
    isLoading: boolean;
    handleKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
}

export const Composer = ({ input, setInput, handleSend, isLoading, handleKeyDown }: ComposerProps) => (
    <div className="chat-input-wrapper">
        <div className="input-box">
            <input
                type="text"
                className="chat-input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a plain-language question…"
                disabled={isLoading}
            />
            <button
                className="send-btn"
                onClick={() => handleSend(input)}
                disabled={!input.trim() || isLoading}
            >
                <Send size={16} />
            </button>
        </div>
        <div className="chat-footer">
            <div className="footer-item">
                <Lock size={12} /> End-to-End Encrypted
            </div>
            <div className="footer-item">
                <ShieldCheck size={12} /> No data leaves your machine
            </div>
            <div className="footer-item">
                <Zap size={12} /> Deterministic Output
            </div>
        </div>
    </div>
);
