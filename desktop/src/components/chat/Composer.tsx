import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
    Send,
    Lock,
    ShieldCheck,
    Zap,
    FileText,
    Globe,
    X,
    ChevronDown,
    ChevronUp,
    Database,
    Layers,
    Search,
    CheckSquare,
    Square
} from 'lucide-react';

export interface ScopeFile {
    file_id: string;
    filename: string;
    chunk_count: number;
    source_type?: string;
    group_name?: string;
    table_name?: string;
}

interface ComposerProps {
    input: string;
    setInput: (val: string) => void;
    handleSend: (text?: string) => void;
    isLoading: boolean;
    handleKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
    availableFiles?: ScopeFile[];
    selectedFileIds?: string[];
    onSelectFiles?: (fileIds: string[]) => void;
}

export const Composer = ({
    input,
    setInput,
    handleSend,
    isLoading,
    handleKeyDown,
    availableFiles = [],
    selectedFileIds = [],
    onSelectFiles
}: ComposerProps) => {
    const [isOpen, setIsOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [expandedDbGroups, setExpandedDbGroups] = useState<Record<string, boolean>>({});
    const popoverRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);

    // Close popover when clicking outside
    useEffect(() => {
        const handleClickOutside = (e: MouseEvent) => {
            if (
                popoverRef.current &&
                !popoverRef.current.contains(e.target as Node) &&
                triggerRef.current &&
                !triggerRef.current.contains(e.target as Node)
            ) {
                setIsOpen(false);
            }
        };
        if (isOpen) {
            document.addEventListener('mousedown', handleClickOutside);
        }
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [isOpen]);

    const totalKbChunks = useMemo(
        () => availableFiles.reduce((acc, f) => acc + (f.chunk_count || 0), 0),
        [availableFiles]
    );

    const selectedFiles = useMemo(
        () => availableFiles.filter(f => selectedFileIds.includes(f.file_id)),
        [availableFiles, selectedFileIds]
    );

    const selectedTotalChunks = useMemo(
        () => selectedFiles.reduce((acc, f) => acc + (f.chunk_count || 0), 0),
        [selectedFiles]
    );

    // Group ALL available files by Database / Files
    const allDbGroups = useMemo(() => {
        const dbGroups: Record<string, ScopeFile[]> = {};
        const standaloneFiles: ScopeFile[] = [];

        availableFiles.forEach(f => {
            if (f.source_type === 'database' || f.group_name) {
                const grp = f.group_name || 'Database';
                if (!dbGroups[grp]) dbGroups[grp] = [];
                dbGroups[grp].push(f);
            } else {
                standaloneFiles.push(f);
            }
        });

        return { dbGroups, standaloneFiles };
    }, [availableFiles]);

    // Detect database groups that are completely selected
    const fullySelectedGroups = useMemo(() => {
        const groups: {
            name: string;
            files: ScopeFile[];
            fileIds: string[];
            chunks: number;
        }[] = [];

        Object.entries(allDbGroups.dbGroups).forEach(([grpName, grpFiles]) => {
            if (grpFiles.length > 1) {
                const grpIds = grpFiles.map(f => f.file_id);
                if (grpIds.every(id => selectedFileIds.includes(id))) {
                    const chunks = grpFiles.reduce((acc, f) => acc + (f.chunk_count || 0), 0);
                    groups.push({
                        name: grpName,
                        files: grpFiles,
                        fileIds: grpIds,
                        chunks
                    });
                }
            }
        });

        return groups;
    }, [allDbGroups, selectedFileIds]);

    // Single active database group when the entire selection is just this database
    const activeDbGroup = useMemo(() => {
        if (fullySelectedGroups.length === 1) {
            const grp = fullySelectedGroups[0];
            if (selectedFiles.length === grp.files.length) {
                return { name: grp.name, count: grp.files.length, chunks: grp.chunks, files: grp.files };
            }
        }
        return null;
    }, [fullySelectedGroups, selectedFiles]);

    // File IDs that belong to fully selected database groups
    const fullySelectedGroupFileIds = useMemo(() => {
        const set = new Set<string>();
        fullySelectedGroups.forEach(grp => {
            grp.fileIds.forEach(id => set.add(id));
        });
        return set;
    }, [fullySelectedGroups]);

    // Selected files that are NOT part of a fully selected database group (or individual standalone files)
    const remainingSelectedFiles = useMemo(() => {
        return selectedFiles.filter(f => !fullySelectedGroupFileIds.has(f.file_id));
    }, [selectedFiles, fullySelectedGroupFileIds]);

    // Filtered files for search inside popover
    const filteredFiles = useMemo(() => {
        if (!searchQuery.trim()) return availableFiles;
        const q = searchQuery.toLowerCase().trim();
        return availableFiles.filter(
            f =>
                f.filename.toLowerCase().includes(q) ||
                (f.group_name && f.group_name.toLowerCase().includes(q)) ||
                (f.table_name && f.table_name.toLowerCase().includes(q))
        );
    }, [availableFiles, searchQuery]);

    // Group files by Database / Files for the popover list
    const groupedFiles = useMemo(() => {
        const dbGroups: Record<string, ScopeFile[]> = {};
        const standaloneFiles: ScopeFile[] = [];

        filteredFiles.forEach(f => {
            if (f.source_type === 'database' || f.group_name) {
                const grp = f.group_name || 'Database';
                if (!dbGroups[grp]) dbGroups[grp] = [];
                dbGroups[grp].push(f);
            } else {
                standaloneFiles.push(f);
            }
        });

        return { dbGroups, standaloneFiles };
    }, [filteredFiles]);

    const handleToggleFile = (fileId: string) => {
        if (!onSelectFiles) return;
        if (selectedFileIds.includes(fileId)) {
            onSelectFiles(selectedFileIds.filter(id => id !== fileId));
        } else {
            onSelectFiles([...selectedFileIds, fileId]);
        }
    };

    const handleSelectAll = () => {
        if (!onSelectFiles) return;
        onSelectFiles(availableFiles.map(f => f.file_id));
    };

    const handleClearSelection = () => {
        if (!onSelectFiles) return;
        onSelectFiles([]);
    };

    const handleRemoveFile = (fileId: string, e: React.MouseEvent) => {
        e.stopPropagation();
        if (!onSelectFiles) return;
        onSelectFiles(selectedFileIds.filter(id => id !== fileId));
    };

    const handleRemoveDbGroup = (grpFiles: ScopeFile[], e: React.MouseEvent) => {
        e.stopPropagation();
        if (!onSelectFiles) return;
        const groupFileIds = new Set(grpFiles.map(f => f.file_id));
        onSelectFiles(selectedFileIds.filter(id => !groupFileIds.has(id)));
    };

    const toggleExpandDb = (grpName: string) => {
        setExpandedDbGroups(prev => ({
            ...prev,
            [grpName]: !prev[grpName]
        }));
    };

    // Formulate placeholder text based on scoping
    const inputPlaceholder = useMemo(() => {
        if (selectedFiles.length === 0) {
            return "Type a plain-language question…";
        }
        if (activeDbGroup) {
            return `Ask question across whole ${activeDbGroup.name} database (${activeDbGroup.chunks.toLocaleString()} chunks)…`;
        }
        if (selectedFiles.length === 1) {
            return `Ask question specifically about ${selectedFiles[0].filename}…`;
        }
        const previewNames = selectedFiles.slice(0, 2).map(f => f.table_name || f.filename).join(', ');
        const extra = selectedFiles.length > 2 ? ` +${selectedFiles.length - 2} more` : '';
        return `Ask question across ${selectedFiles.length} sources (${previewNames}${extra})…`;
    }, [selectedFiles, activeDbGroup]);

    return (
        <div className="chat-input-wrapper">
            {availableFiles.length > 0 && onSelectFiles && (
                <div className="multi-scope-wrapper">
                    <div className="scope-selector-container">
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', color: '#4B5563' }}>
                            {selectedFiles.length === 0 ? (
                                <Globe size={14} color="#6B7280" />
                            ) : selectedFiles.length === 1 ? (
                                selectedFiles[0].source_type === 'database' ? (
                                    <Database size={14} color="#0D7377" />
                                ) : (
                                    <FileText size={14} color="#0D7377" />
                                )
                            ) : (
                                <Layers size={14} color="#0D7377" />
                            )}
                            <span style={{ fontWeight: 600 }}>Data Scope:</span>
                        </div>

                        {/* Custom Dropdown Trigger */}
                        <button
                            type="button"
                            ref={triggerRef}
                            className={`multi-scope-trigger ${selectedFiles.length > 0 ? 'active' : ''}`}
                            onClick={() => setIsOpen(prev => !prev)}
                            disabled={isLoading}
                        >
                            <span className="trigger-label">
                                {selectedFiles.length === 0 ? (
                                    <>🌐 All Knowledge Base ({totalKbChunks.toLocaleString()} chunks)</>
                                ) : activeDbGroup ? (
                                    <>⭐ {activeDbGroup.name} (Whole Database — {activeDbGroup.chunks.toLocaleString()} chunks)</>
                                ) : selectedFiles.length === 1 ? (
                                    <>
                                        {selectedFiles[0].source_type === 'database' ? '📊' : '📄'}{' '}
                                        {selectedFiles[0].filename} ({selectedFiles[0].chunk_count.toLocaleString()} chunks)
                                    </>
                                ) : (
                                    <>
                                        📚 {selectedFiles.length} Sources Selected ({selectedTotalChunks.toLocaleString()} chunks)
                                    </>
                                )}
                            </span>
                            <ChevronDown size={13} className={`trigger-chevron ${isOpen ? 'open' : ''}`} />
                        </button>

                        {selectedFiles.length > 0 && (
                            <button
                                type="button"
                                className="scope-clear-pill"
                                onClick={handleClearSelection}
                                title="Reset to All Knowledge Base"
                            >
                                <X size={11} /> Reset
                            </button>
                        )}
                    </div>

                    {/* Popover Dropdown */}
                    {isOpen && (
                        <div className="multi-scope-popover" ref={popoverRef}>
                            <div className="popover-header">
                                <div className="popover-title">
                                    <Layers size={15} color="#0D7377" />
                                    <span>Select Knowledge Sources</span>
                                </div>
                                <div className="popover-actions">
                                    <button
                                        type="button"
                                        className="action-btn"
                                        onClick={handleSelectAll}
                                        title="Select all sources"
                                    >
                                        <CheckSquare size={12} /> Select All
                                    </button>
                                    <button
                                        type="button"
                                        className="action-btn"
                                        onClick={handleClearSelection}
                                        title="Clear all selection to use All KB"
                                    >
                                        <Square size={12} /> Clear (All KB)
                                    </button>
                                </div>
                            </div>

                            {/* Search Filter */}
                            <div className="popover-search">
                                <Search size={13} color="#9CA3AF" />
                                <input
                                    type="text"
                                    placeholder="Filter tables or files…"
                                    value={searchQuery}
                                    onChange={e => setSearchQuery(e.target.value)}
                                    autoFocus
                                />
                                {searchQuery && (
                                    <button
                                        type="button"
                                        onClick={() => setSearchQuery('')}
                                        className="search-clear-btn"
                                    >
                                        <X size={12} />
                                    </button>
                                )}
                            </div>

                            {/* Options List */}
                            <div className="popover-list">
                                {/* Option for All Knowledge Base */}
                                <div
                                    className={`popover-item ${selectedFileIds.length === 0 ? 'selected' : ''}`}
                                    onClick={handleClearSelection}
                                >
                                    <input
                                        type="checkbox"
                                        className="item-checkbox"
                                        checked={selectedFileIds.length === 0}
                                        onChange={() => {}}
                                    />
                                    <div className="item-icon">
                                        <Globe size={14} color="#2563EB" />
                                    </div>
                                    <div className="item-info">
                                        <div className="item-name">All Knowledge Base (Everything)</div>
                                        <div className="item-sub">
                                            {totalKbChunks.toLocaleString()} total chunks across all sources
                                        </div>
                                    </div>
                                </div>

                                <div className="popover-divider" />

                                {/* Database Groups */}
                                {Object.entries(groupedFiles.dbGroups).map(([groupName, files]) => {
                                    const ids = files.map(f => f.file_id);
                                    const allSelected = ids.length > 0 && ids.every(id => selectedFileIds.includes(id));
                                    const groupChunks = files.reduce((acc, curr) => acc + (curr.chunk_count || 0), 0);

                                    const handleToggleWholeGroup = () => {
                                        if (allSelected) {
                                            onSelectFiles(selectedFileIds.filter(id => !ids.includes(id)));
                                        } else {
                                            const combined = Array.from(new Set([...selectedFileIds, ...ids]));
                                            onSelectFiles(combined);
                                        }
                                    };

                                    return (
                                        <div key={groupName} className="popover-group">
                                            <div className="group-header">
                                                <div className="group-title">
                                                    <Database size={13} color="#0D7377" />
                                                    <span>{groupName}</span>
                                                </div>
                                                <button
                                                    type="button"
                                                    className="group-select-all"
                                                    onClick={e => {
                                                        e.stopPropagation();
                                                        handleToggleWholeGroup();
                                                    }}
                                                >
                                                    {allSelected ? 'Deselect Group' : 'Select Group'}
                                                </button>
                                            </div>

                                            {/* Whole Database Selectable Row */}
                                            <div
                                                className={`popover-item whole-db-item ${allSelected ? 'selected' : ''}`}
                                                style={{
                                                    background: allSelected ? 'rgba(13, 115, 119, 0.12)' : 'rgba(241, 245, 249, 0.65)',
                                                    borderLeft: allSelected ? '3px solid #0D7377' : '3px solid transparent',
                                                    margin: '0.2rem 0 0.4rem',
                                                    padding: '0.45rem 0.65rem'
                                                }}
                                                onClick={handleToggleWholeGroup}
                                            >
                                                <input
                                                    type="checkbox"
                                                    className="item-checkbox"
                                                    checked={allSelected}
                                                    onChange={() => {}}
                                                />
                                                <div className="item-icon">
                                                    <Database size={14} color="#0D7377" />
                                                </div>
                                                <div className="item-info">
                                                    <div className="item-name" style={{ color: '#0F172A', fontWeight: 700, fontSize: '0.86rem' }}>
                                                        ⭐ {groupName} (Whole Database)
                                                    </div>
                                                    <div className="item-sub" style={{ color: '#0D7377', fontWeight: 500 }}>
                                                        {groupChunks.toLocaleString()} chunks across all {files.length} tables
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Individual Tables */}
                                            {files.map(f => {
                                                const isChecked = selectedFileIds.includes(f.file_id);
                                                return (
                                                    <div
                                                        key={f.file_id}
                                                        className={`popover-item ${isChecked ? 'selected' : ''}`}
                                                        style={{ paddingLeft: '1.25rem' }}
                                                        onClick={() => handleToggleFile(f.file_id)}
                                                    >
                                                        <input
                                                            type="checkbox"
                                                            className="item-checkbox"
                                                            checked={isChecked}
                                                            onChange={() => {}}
                                                        />
                                                        <div className="item-icon">
                                                            <span style={{ fontSize: '0.75rem', color: '#94A3B8' }}>↳</span>
                                                        </div>
                                                        <div className="item-info">
                                                            <div className="item-name">{f.table_name || f.filename}</div>
                                                            <div className="item-sub">
                                                                {f.chunk_count > 0 ? (
                                                                    `${f.chunk_count.toLocaleString()} chunks`
                                                                ) : (
                                                                    <span style={{ color: '#9CA3AF' }}>Empty table (0 chunks)</span>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </div>
                                    );
                                })}

                                {/* Standalone Uploaded Files */}
                                {groupedFiles.standaloneFiles.length > 0 && (
                                    <div className="popover-group">
                                        <div className="group-header">
                                            <div className="group-title">
                                                <FileText size={13} color="#4B5563" />
                                                <span>Uploaded Files</span>
                                            </div>
                                        </div>
                                        {groupedFiles.standaloneFiles.map(f => {
                                            const isChecked = selectedFileIds.includes(f.file_id);
                                            return (
                                                <div
                                                    key={f.file_id}
                                                    className={`popover-item ${isChecked ? 'selected' : ''}`}
                                                    onClick={() => handleToggleFile(f.file_id)}
                                                >
                                                    <input
                                                        type="checkbox"
                                                        className="item-checkbox"
                                                        checked={isChecked}
                                                        onChange={() => {}}
                                                    />
                                                    <div className="item-icon">
                                                        <FileText size={13} color="#4B5563" />
                                                    </div>
                                                    <div className="item-info">
                                                        <div className="item-name">{f.filename}</div>
                                                        <div className="item-sub">
                                                            {f.chunk_count.toLocaleString()} chunks
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                    </div>
                                )}

                                {filteredFiles.length === 0 && (
                                    <div className="popover-empty">
                                        No sources match "{searchQuery}"
                                    </div>
                                )}
                            </div>

                            {/* Popover Footer */}
                            <div className="popover-footer">
                                <span className="selection-count">
                                    {selectedFileIds.length === 0 ? (
                                        <>Scope: <strong>All Knowledge Base</strong></>
                                    ) : (
                                        <>Selected: <strong>{selectedFileIds.length}</strong> of {availableFiles.length} sources ({selectedTotalChunks.toLocaleString()} chunks)</>
                                    )}
                                </span>
                                <button
                                    type="button"
                                    className="done-btn"
                                    onClick={() => setIsOpen(false)}
                                >
                                    Done
                                </button>
                            </div>
                        </div>
                    )}

                    {/* Selected Source Pills / Chips */}
                    {selectedFiles.length > 0 && (
                        <div className="selected-chips-bar">
                            <span className="chips-label">Active Scope:</span>
                            <div className="chips-scroll">
                                {/* Fully selected whole database groups */}
                                {fullySelectedGroups.map(grp => {
                                    const isExpanded = !!expandedDbGroups[grp.name];
                                    return (
                                        <React.Fragment key={grp.name}>
                                            <span
                                                className={`source-chip database-group-chip ${isExpanded ? 'expanded' : ''}`}
                                                title={`${grp.name} (Whole Database — ${grp.files.length} tables, ${grp.chunks.toLocaleString()} chunks)`}
                                            >
                                                <Database size={12} className="chip-icon-db" />
                                                <span className="chip-name">
                                                    <strong>{grp.name}</strong> <span className="chip-sub">(Whole Database &bull; {grp.files.length} tables)</span>
                                                </span>
                                                <span className="chip-count">{grp.chunks.toLocaleString()} chunks</span>
                                                <button
                                                    type="button"
                                                    className="chip-remove"
                                                    onClick={e => handleRemoveDbGroup(grp.files, e)}
                                                    title={`Remove entire ${grp.name} database`}
                                                >
                                                    <X size={11} />
                                                </button>
                                            </span>

                                            <button
                                                type="button"
                                                className={`tables-toggle-btn ${isExpanded ? 'active' : ''}`}
                                                onClick={() => toggleExpandDb(grp.name)}
                                                title={isExpanded ? 'Hide individual tables' : 'Show individual tables'}
                                            >
                                                <span>{isExpanded ? 'Hide tables' : `Tables (${grp.files.length})`}</span>
                                                {isExpanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                            </button>

                                            {/* Sub-expanded tables if user toggles to see individual tables */}
                                            {isExpanded && grp.files.map(f => (
                                                <span
                                                    key={f.file_id}
                                                    className="source-chip table-chip-sub"
                                                    title={`${f.filename} (${f.chunk_count} chunks)`}
                                                >
                                                    <span className="chip-icon">📊</span>
                                                    <span className="chip-name">{f.table_name || f.filename}</span>
                                                    <span className="chip-count">{f.chunk_count}</span>
                                                    <button
                                                        type="button"
                                                        className="chip-remove"
                                                        onClick={e => handleRemoveFile(f.file_id, e)}
                                                        title={`Remove ${f.table_name || f.filename}`}
                                                    >
                                                        <X size={10} />
                                                    </button>
                                                </span>
                                            ))}
                                        </React.Fragment>
                                    );
                                })}

                                {/* Remaining individual files / tables not part of a whole database */}
                                {remainingSelectedFiles.map(f => (
                                    <span key={f.file_id} className="source-chip" title={`${f.filename} (${f.chunk_count} chunks)`}>
                                        <span className="chip-icon">
                                            {f.source_type === 'database' ? '📊' : '📄'}
                                        </span>
                                        <span className="chip-name">{f.table_name || f.filename}</span>
                                        <span className="chip-count">{f.chunk_count}</span>
                                        <button
                                            type="button"
                                            className="chip-remove"
                                            onClick={e => handleRemoveFile(f.file_id, e)}
                                            title="Remove source"
                                        >
                                            <X size={10} />
                                        </button>
                                    </span>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            )}

            <div className="input-box">
                <input
                    type="text"
                    className="chat-input"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={inputPlaceholder}
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
};
