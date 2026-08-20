import { ArrowRight } from 'lucide-react';

interface Props {
    mapping: Record<string, string>; // { originalCol: canonicalCol }
}

// Renders the two-column mapping review table
export const MappingTable = ({ mapping }: Props) => {
    const entries = Object.entries(mapping);
    return (
        <div style={{ border: '1px solid var(--border-color)', borderRadius: '8px', overflow: 'hidden' }}>
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
                            <td>{orig}</td>
                            <td className="mapping-arrow"><ArrowRight size={14} /></td>
                            <td style={{ color: 'var(--accent-teal)', fontWeight: 600 }}>{canonical}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
};
