import { ArrowRight } from 'lucide-react';

interface Props {
    mapping: Record<string, string>; // { originalCol: canonicalCol }
    columns?: string[];
    fields?: string[];
    onChange?: (mapping: Record<string, string>) => void;
}

// Renders the two-column mapping review table
export const MappingTable = ({ mapping, columns, fields, onChange }: Props) => {
    const entries = (columns || Object.keys(mapping)).filter(c => !['source_row', 'source_connector'].includes(c)).map(c => [c, mapping[c] || '']);
    return (
        <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', overflowX: 'auto' }}>
            <table className="mapping-table">
                <thead>
                    <tr>
                        <th>Your File Column</th>
                        <th></th>
                        <th>System Column</th>
                    </tr>
                </thead>
                <tbody>
                    {entries.map(([orig, canonical], i) => (
                        <tr key={i}>
                            <td style={{ overflowWrap: 'anywhere' }}>{orig}</td>
                            <td className="mapping-arrow"><ArrowRight size={14} /></td>
                            <td style={{ color: 'var(--accent-teal)', fontWeight: 600 }}>
                                {onChange ? <select className="sheet-select" aria-label={`Map ${orig} to system field`} value={canonical}
                                    onChange={event => {
                                        const next = { ...mapping };
                                        if (event.target.value) next[orig] = event.target.value;
                                        else delete next[orig];
                                        onChange(next);
                                    }}>
                                    <option value="">Keep as extra column</option>
                                    {(fields || []).filter(f => !['source_row', 'source_connector'].includes(f)).map(field => (
                                        <option key={field} value={field} disabled={field !== canonical && Object.values(mapping).includes(field)}>{field.replaceAll('_', ' ')}</option>
                                    ))}
                                </select> : canonical || 'Extra column'}
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};
