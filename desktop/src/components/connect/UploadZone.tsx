import React, { useRef, useState } from 'react';
import { CloudUpload, File, X } from 'lucide-react';

interface Props {
    file: File | null;
    onFileChange: (f: File | null) => void;
}

// Formats byte count into KB / MB string
const formatBytes = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
};

// Drag-and-drop file upload zone
export const UploadZone = ({ file, onFileChange }: Props) => {
    const inputRef = useRef<HTMLInputElement>(null);
    const [dragOver, setDragOver] = useState(false);

    const handle = (f: File | null) => {
        if (!f) return onFileChange(null);
        // Only accept CSV, Excel, JSON, SQLite
        const ok = /\.(csv|xlsx|xls|json|db)$/i.test(f.name);
        if (ok) onFileChange(f);
        else alert('Unsupported file type. Please use CSV, Excel (.xlsx), JSON, or SQLite (.db)');
    };

    const onDrop = (e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        handle(e.dataTransfer.files[0] ?? null);
    };

    return (
        <div
            className={`upload-zone ${dragOver ? 'drag-over' : ''}`}
            onDragOver={e => { e.preventDefault(); setDragOver(true); }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            onClick={() => !file && inputRef.current?.click()}
        >
            <input
                ref={inputRef}
                type="file"
                accept=".csv,.xlsx,.xls,.json,.db"
                className="file-hidden"
                onChange={e => handle(e.target.files?.[0] ?? null)}
                onClick={e => e.stopPropagation()}
            />

            {file ? (
                <div className="file-selected-info">
                    <div className="file-icon-badge"><File size={20} /></div>
                    <div className="file-meta">
                        <div className="file-name">{file.name}</div>
                        <div className="file-size">{formatBytes(file.size)} · {file.name.split('.').pop()?.toUpperCase()}</div>
                    </div>
                    <button
                        className="remove-file-btn"
                        onClick={e => { e.stopPropagation(); onFileChange(null); }}
                        title="Remove file"
                    >
                        <X size={16} />
                    </button>
                </div>
            ) : (
                <>
                    <CloudUpload className="upload-zone-icon" size={48} />
                    <div className="upload-zone-text">Drag &amp; drop your file here, or click to browse</div>
                    <div className="upload-zone-hint">Accepted: CSV, Excel (.xlsx), JSON, SQLite (.db)</div>
                </>
            )}
        </div>
    );
};
