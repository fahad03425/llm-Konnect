import React, { useState, useRef, useEffect, useMemo } from 'react';
import {
    Send,
    ShieldCheck,
    FileText,
    Globe,
    X,
    ChevronDown,
    ChevronUp,
    Database,
    Layers,
    CheckSquare,
    Square,
    Boxes
} from 'lucide-react';
import { getCustomDbGroups, type CustomDbGroup } from '../../utils/dbGroups';

export interface ScopeFile {
    file_id: string;
    filename: string;
    chunk_count: number;
    source_type?: string;
    group_name?: string;
    table_name?: string;
    file_path?: string;
    domain?: string;
}

interface ComposerProps {
    input: string;
    setInput: (val: string) => void;
    handleSend: (text?: string) => void;
    handleStopGeneration?: () => void;
    isLoading: boolean;
    handleKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => void;
    availableFiles?: ScopeFile[];
    selectedFileIds?: string[];
    onSelectFiles?: (fileIds: string[]) => void;
    inputRef?: React.RefObject<HTMLInputElement | null> | React.Ref<HTMLInputElement>;
}

export const Composer = ({
    input,
    setInput,
    handleSend,
    handleStopGeneration,
    isLoading,
    handleKeyDown,
    availableFiles = [],
    selectedFileIds = [],
    onSelectFiles,
    inputRef
}: ComposerProps) => {
    const [isOpen, setIsOpen] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [expandedDbGroups, setExpandedDbGroups] = useState<Record<string, boolean>>({});
    const [customDbGroups, setCustomDbGroups] = useState<CustomDbGroup[]>(() => getCustomDbGroups());
    const popoverRef = useRef<HTMLDivElement>(null);
    const triggerRef = useRef<HTMLButtonElement>(null);

    // Listen for custom database group updates
    useEffect(() => {
        const handleGroupsChanged = () => {
            setCustomDbGroups(getCustomDbGroups());
        };
        window.addEventListener('custom-db-groups-changed', handleGroupsChanged);
        return () => {
            window.removeEventListener('custom-db-groups-changed', handleGroupsChanged);
        };
    }, []);

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

    // Detect if a custom database group is active
    const activeCustomGroup = useMemo(() => {
        for (const grp of customDbGroups) {
            const memberFiles = availableFiles.filter(f => 
                f.group_name && grp.dbNames.some(d => d.toLowerCase().trim() === f.group_name?.toLowerCase().trim())
            );
            if (memberFiles.length > 0) {
                const memberIds = memberFiles.map(f => f.file_id);
                if (memberIds.length === selectedFiles.length && memberIds.every(id => selectedFileIds.includes(id))) {
                    const chunks = memberFiles.reduce((acc, f) => acc + (f.chunk_count || 0), 0);
                    return { name: grp.name, count: memberFiles.length, chunks, dbNames: grp.dbNames, fileIds: memberIds };
                }
            }
        }
        return null;
    }, [customDbGroups, availableFiles, selectedFileIds, selectedFiles]);

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
        if (activeCustomGroup) return null;
        if (fullySelectedGroups.length === 1) {
            const grp = fullySelectedGroups[0];
            if (selectedFiles.length === grp.files.length) {
                return { name: grp.name, count: grp.files.length, chunks: grp.chunks, files: grp.files };
            }
        }
        return null;
    }, [activeCustomGroup, fullySelectedGroups, selectedFiles]);

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
        if (activeCustomGroup) {
            const customGroupFileIdSet = new Set(activeCustomGroup.fileIds);
            return selectedFiles.filter(f => !customGroupFileIdSet.has(f.file_id));
        }
        return selectedFiles.filter(f => !fullySelectedGroupFileIds.has(f.file_id));
    }, [selectedFiles, activeCustomGroup, fullySelectedGroupFileIds]);

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
        if (activeCustomGroup) {
            return `Ask question across whole ${activeCustomGroup.name} (${activeCustomGroup.chunks.toLocaleString()} chunks)…`;
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
    }, [selectedFiles, activeCustomGroup, activeDbGroup]);

    return (
        <div className="chat-input-wrapper">
            {availableFiles.length > 0 && onSelectFiles && (
                <div className="multi-scope-wrapper">
                    <div className="scope-selector-container">
                        <div className="scope-label-wrapper">
                            {selectedFiles.length === 0 ? (
                                <Globe size={14} className="scope-icon" />
                            ) : activeCustomGroup ? (
                                <Boxes size={14} className="scope-icon active" style={{ color: 'var(--brand-green)' }} />
                            ) : selectedFiles.length === 1 ? (
                                selectedFiles[0].source_type === 'database' ? (
                                    <Database size={14} className="scope-icon active" />
                                ) : (
                                    <FileText size={14} className="scope-icon active" />
                                )
                            ) : (
                                <Layers size={14} className="scope-icon active" />
                            )}
                            <span className="scope-label-text">Data Scope:</span>
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
                                    <>All Knowledge Base ({totalKbChunks.toLocaleString()} chunks)</>
                                ) : activeCustomGroup ? (
                                    <>{activeCustomGroup.name} (Consolidated Database — {activeCustomGroup.chunks.toLocaleString()} chunks)</>
                                ) : activeDbGroup ? (
                                    <>{activeDbGroup.name} (Whole Database — {activeDbGroup.chunks.toLocaleString()} chunks)</>
                                ) : selectedFiles.length === 1 ? (
                                    <>
                                        {selectedFiles[0].filename} ({selectedFiles[0].chunk_count.toLocaleString()} chunks)
                                    </>
                                ) : (
                                    <>
                                        {selectedFiles.length} Sources Selected ({selectedTotalChunks.toLocaleString()} chunks)
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
                                    <Layers size={15} className="popover-title-icon" />
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
                                    className={`popover-item kb-all-item ${selectedFileIds.length === 0 ? 'selected' : ''}`}
                                    onClick={handleClearSelection}
                                >
                                    <input
                                        type="checkbox"
                                        className="item-checkbox"
                                        checked={selectedFileIds.length === 0}
                                        onChange={() => {}}
                                    />
                                    <div className="item-icon">
                                        <Globe size={14} />
                                    </div>
                                    <div className="item-info">
                                        <div className="item-name">All Knowledge Base (Everything)</div>
                                        <div className="item-sub">
                                            {totalKbChunks.toLocaleString()} total chunks across all sources
                                        </div>
                                    </div>
                                </div>

                                <div className="popover-divider" />

                                {/* Custom Database Groups (Consolidated Single Option) */}
                                {customDbGroups.length > 0 && (
                                    <>
                                        <div className="group-header" style={{ margin: '0.4rem 0 0.2rem' }}>
                                            <div className="group-title" style={{ color: 'var(--brand-green)', fontWeight: 600 }}>
                                                <Boxes size={13} className="group-title-icon" style={{ color: 'var(--brand-green)' }} />
                                                <span>Consolidated Database Groups</span>
                                            </div>
                                        </div>
                                        {customDbGroups.map(grp => {
                                            const memberFiles = availableFiles.filter(f => 
                                                f.group_name && grp.dbNames.some(d => d.toLowerCase().trim() === f.group_name?.toLowerCase().trim())
                                            );
                                            if (memberFiles.length === 0) return null;
                                            const ids = memberFiles.map(f => f.file_id);
                                            const allSelected = ids.length > 0 && ids.every(id => selectedFileIds.includes(id));
                                            const groupChunks = memberFiles.reduce((acc, curr) => acc + (curr.chunk_count || 0), 0);

                                            const handleToggleCustomGroup = () => {
                                                if (!onSelectFiles) return;
                                                if (allSelected) {
                                                    onSelectFiles(selectedFileIds.filter(id => !ids.includes(id)));
                                                } else {
                                                    const combined = Array.from(new Set([...selectedFileIds, ...ids]));
                                                    onSelectFiles(combined);
                                                }
                                            };

                                            return (
                                                <div key={grp.id} className="popover-group">
                                                    <div
                                                        className={`popover-item whole-db-item ${allSelected ? 'selected' : ''}`}
                                                        onClick={handleToggleCustomGroup}
                                                        style={{ borderLeft: '3px solid var(--brand-green)', background: allSelected ? 'rgba(16, 185, 129, 0.12)' : 'rgba(16, 185, 129, 0.04)' }}
                                                    >
                                                        <input
                                                            type="checkbox"
                                                            className="item-checkbox"
                                                            checked={allSelected}
                                                            onChange={() => {}}
                                                        />
                                                        <div className="item-icon" style={{ color: 'var(--brand-green)' }}>
                                                            <Boxes size={15} />
                                                        </div>
                                                        <div className="item-info">
                                                            <div className="item-name" style={{ fontWeight: 600 }}>
                                                                {grp.name} (Consolidated Group)
                                                            </div>
                                                            <div className="item-sub">
                                                                {groupChunks.toLocaleString()} chunks across {grp.dbNames.join(' + ')} ({memberFiles.length} tables)
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            );
                                        })}
                                        <div className="popover-divider" />
                                    </>
                                )}

                                {/* Individual Database Groups (only show if not part of a custom consolidated group) */}
                                {Object.entries(groupedFiles.dbGroups)
                                    .filter(([groupName]) => {
                                        const isGrouped = customDbGroups.some(grp =>
                                            grp.dbNames.some(d => d.toLowerCase().trim() === groupName.toLowerCase().trim())
                                        );
                                        return !isGrouped;
                                    })
                                    .map(([groupName, files]) => {
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
                                            {/* Whole Database Selectable Row */}
                                            <div
                                                className={`popover-item whole-db-item ${allSelected ? 'selected' : ''}`}
                                                onClick={handleToggleWholeGroup}
                                            >
                                                <input
                                                    type="checkbox"
                                                    className="item-checkbox"
                                                    checked={allSelected}
                                                    onChange={() => {}}
                                                />
                                                <div className="item-icon">
                                                    <Database size={14} />
                                                </div>
                                                <div className="item-info">
                                                    <div className="item-name">
                                                        {groupName} (Whole Database)
                                                    </div>
                                                    <div className="item-sub">
                                                        {groupChunks.toLocaleString()} chunks across all {files.length} tables
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })}

                                {/* Standalone Uploaded Files */}
                                {groupedFiles.standaloneFiles.length > 0 && (
                                    <div className="popover-group">
                                        <div className="group-header">
                                            <div className="group-title">
                                                <FileText size={13} className="group-title-icon" />
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
                                                    <div className="item-icon file-icon">
                                                        <FileText size={13} />
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
                                {/* Custom Consolidated Database Group (Single Chip) */}
                                {activeCustomGroup ? (
                                    <React.Fragment>
                                        <span
                                            className={`source-chip database-group-chip ${expandedDbGroups[activeCustomGroup.name] ? 'expanded' : ''}`}
                                            style={{ borderColor: 'var(--brand-green)', background: 'rgba(16, 185, 129, 0.1)' }}
                                            title={`${activeCustomGroup.name} (Consolidated Group — ${activeCustomGroup.count} tables across ${activeCustomGroup.dbNames.join(', ')}, ${activeCustomGroup.chunks.toLocaleString()} chunks)`}
                                        >
                                            <Boxes size={13} className="chip-icon-db" style={{ color: 'var(--brand-green)' }} />
                                            <span className="chip-name">
                                                <strong style={{ color: 'var(--brand-green)' }}>{activeCustomGroup.name}</strong> <span className="chip-sub">(Consolidated &bull; {activeCustomGroup.count} tables)</span>
                                            </span>
                                            <span className="chip-count" style={{ color: 'var(--brand-green)' }}>{activeCustomGroup.chunks.toLocaleString()} chunks</span>
                                            <button
                                                type="button"
                                                className="chip-remove"
                                                onClick={handleClearSelection}
                                                title={`Remove entire ${activeCustomGroup.name} group`}
                                            >
                                                <X size={11} />
                                            </button>
                                        </span>

                                        <button
                                            type="button"
                                            className={`tables-toggle-btn ${expandedDbGroups[activeCustomGroup.name] ? 'active' : ''}`}
                                            onClick={() => toggleExpandDb(activeCustomGroup.name)}
                                            title={expandedDbGroups[activeCustomGroup.name] ? 'Hide individual tables' : 'Show individual tables'}
                                        >
                                            <span>{expandedDbGroups[activeCustomGroup.name] ? 'Hide tables' : `Tables (${activeCustomGroup.count})`}</span>
                                            {expandedDbGroups[activeCustomGroup.name] ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
                                        </button>

                                        {/* Sub-expanded tables if user toggles to see individual tables */}
                                        {expandedDbGroups[activeCustomGroup.name] && selectedFiles.map(f => (
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
                                ) : (
                                    /* Fully selected whole database groups */
                                    fullySelectedGroups.map(grp => {
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
                                    })
                                )}

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
                    ref={inputRef}
                    type="text"
                    className="chat-input"
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={inputPlaceholder}
                    disabled={isLoading}
                />
                {isLoading ? (
                    <button
                        type="button"
                        className="send-btn stop-btn"
                        onClick={handleStopGeneration}
                        title="Stop generation"
                    >
                        <Square size={14} fill="currentColor" />
                    </button>
                ) : (
                    <button
                        type="button"
                        className="send-btn"
                        onClick={() => handleSend(input)}
                        disabled={!input.trim()}
                        title="Send message"
                    >
                        <Send size={16} />
                    </button>
                )}
            </div>
            <div className="chat-footer">
                <div className="footer-item">
                    <ShieldCheck size={12} /> Local AI · 100% offline &amp; private
                </div>
            </div>
        </div>
    );
};
