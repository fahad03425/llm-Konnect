interface Props {
    totalChunks: number | null;
    collectionName: string | null;
}

// Compact KB health card — shown below the wizard
export const KBStatus = ({ totalChunks, collectionName }: Props) => {
    const hasData = totalChunks !== null && totalChunks > 0;
    return (
        <div className="kb-status-card">
            <div className={`kb-dot ${hasData ? 'active' : 'empty'}`} />
            <div>
                <div className="kb-status-text">
                    Knowledge Base:&nbsp;
                    {totalChunks === null
                        ? 'Loading…'
                        : hasData
                            ? `${totalChunks.toLocaleString()} chunks`
                            : 'Empty'}
                </div>
                {collectionName && (
                    <div className="kb-status-sub monospaced">{collectionName}</div>
                )}
            </div>
        </div>
    );
};
