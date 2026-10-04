import { Bot, RefreshCw } from 'lucide-react';

interface TypingIndicatorProps {
    onStop?: () => void;
}

export const TypingIndicator = ({ onStop }: TypingIndicatorProps) => (
    <div className="message-wrapper assistant">
        <div className="message-content">
            <div className="avatar bot">
                <Bot size={16} />
            </div>
            <div className="bubble typing-bubble" style={{ display: 'flex', alignItems: 'center', gap: '0.65rem' }}>
                <div className="typing-indicator">
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                </div>
                {onStop && (
                    <button
                        type="button"
                        className="btn-typing-stop"
                        onClick={onStop}
                        title="Stop generation"
                    >
                        <RefreshCw size={11} className="animate-spin" />
                        <span>Stop</span>
                    </button>
                )}
            </div>
        </div>
    </div>
);
