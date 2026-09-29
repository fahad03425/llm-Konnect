import React, { useState, useEffect } from 'react';
import { Minus, Square, Copy, X } from 'lucide-react';
import './WindowTitleBar.css';

const WindowTitleBar: React.FC = () => {
    const [isMaximized, setIsMaximized] = useState(false);

    useEffect(() => {
        // Detect if running inside Tauri runtime
        const checkTauri = async () => {
            try {
                const { getCurrentWindow } = await import('@tauri-apps/api/window');
                const win = getCurrentWindow();
                // Ensure OS titlebar is removed dynamically
                await win.setDecorations(false);
                const maximized = await win.isMaximized();
                setIsMaximized(maximized);

                const unlisten = await win.onResized(async () => {
                    try {
                        const m = await win.isMaximized();
                        setIsMaximized(m);
                    } catch {}
                });

                return () => {
                    unlisten();
                };
            } catch {
                // Not in Tauri runtime
            }
        };

        checkTauri();
    }, []);

    const handleMinimize = async () => {
        try {
            const { getCurrentWindow } = await import('@tauri-apps/api/window');
            await getCurrentWindow().minimize();
        } catch (err) {
            console.debug('Window minimize called outside Tauri:', err);
        }
    };

    const handleMaximizeToggle = async () => {
        try {
            const { getCurrentWindow } = await import('@tauri-apps/api/window');
            const win = getCurrentWindow();
            await win.toggleMaximize();
            const max = await win.isMaximized();
            setIsMaximized(max);
        } catch (err) {
            console.debug('Window toggleMaximize called outside Tauri:', err);
        }
    };

    const handleClose = async () => {
        try {
            const { getCurrentWindow } = await import('@tauri-apps/api/window');
            await getCurrentWindow().close();
        } catch (err) {
            console.debug('Window close called outside Tauri:', err);
        }
    };

    return (
        <div className="window-titlebar" data-tauri-drag-region onDoubleClick={handleMaximizeToggle}>
            {/* Left: App Identity Icon */}
            <div className="titlebar-left" data-tauri-drag-region>
                <div className="titlebar-icon-wrap" data-tauri-drag-region>
                    <svg className="titlebar-ai-icon" viewBox="0 0 64 64" width="15" height="15" fill="none">
                        <polygon points="34,8 17,32 29,32 31,21" fill="#06b6d4" />
                        <polygon points="29,32 23,54 47,27 34,27" fill="#10b981" />
                        <polygon points="34,8 31,21 47,27 43,8" fill="#8b5cf6" />
                    </svg>
                </div>
            </div>

            {/* Center Drag Region */}
            <div className="titlebar-center" data-tauri-drag-region />

            {/* Right: Google M3 Window Action Controls */}
            <div className="titlebar-controls">
                <button
                    type="button"
                    className="titlebar-btn btn-minimize"
                    onClick={handleMinimize}
                    title="Minimize"
                    aria-label="Minimize Window"
                >
                    <Minus size={13} strokeWidth={2} />
                </button>

                <button
                    type="button"
                    className="titlebar-btn btn-maximize"
                    onClick={handleMaximizeToggle}
                    title={isMaximized ? "Restore Window" : "Maximize Window"}
                    aria-label={isMaximized ? "Restore Window" : "Maximize Window"}
                >
                    {isMaximized ? <Copy size={11} strokeWidth={2} /> : <Square size={11} strokeWidth={2} />}
                </button>

                <button
                    type="button"
                    className="titlebar-btn btn-close"
                    onClick={handleClose}
                    title="Close"
                    aria-label="Close Application"
                >
                    <X size={13} strokeWidth={2} />
                </button>
            </div>
        </div>
    );
};

export default WindowTitleBar;
