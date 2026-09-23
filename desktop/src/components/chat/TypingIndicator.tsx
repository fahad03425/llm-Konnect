import { Bot } from 'lucide-react';

export const TypingIndicator = () => (
    <div className="message-wrapper assistant">
        <div className="message-content">
            <div className="avatar bot">
                <Bot size={16} />
            </div>
            <div className="bubble" style={{ display: 'flex', alignItems: 'center' }}>
                <div className="typing-indicator">
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                    <div className="typing-dot"></div>
                </div>
            </div>
        </div>
    </div>
);
