import { Rows } from 'lucide-react';

interface Props {
    columns: string[];
    sampleRows: (string | number | null)[][];
    totalRows: number;
}

// Renders the first 5 rows preview table
export const PreviewTable = ({ columns, sampleRows, totalRows }: Props) => (
    <div>
        <div className="preview-scroll">
            <table className="preview-table">
                <thead>
                    <tr>
                        {columns.map((col, i) => <th key={i}>{col}</th>)}
                    </tr>
                </thead>
                <tbody>
                    {sampleRows.slice(0, 5).map((row, ri) => (
                        <tr key={ri}>
                            {row.map((cell, ci) => (
                                <td key={ci}>{cell === null ? <span style={{ color: '#9CA3AF' }}>null</span> : String(cell)}</td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
        <div className="preview-meta">
            <Rows size={14} />
            Showing 5 of <strong style={{ color: '#1A1A1A' }}>{totalRows.toLocaleString()}</strong> total rows
            &nbsp;·&nbsp; {columns.length} columns
        </div>
    </div>
);
