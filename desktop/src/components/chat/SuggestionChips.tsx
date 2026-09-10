export const SuggestionChips = ({
    onSelect,
    chips
}: {
    onSelect: (text: string) => void;
    chips?: string[];
}) => {
    const defaultChips = [
        "What is my total revenue?",
        "Which items need immediate attention?",
        "Summarize recent transactions and top categories",
        "Which supplier / vendor has the highest volume?"
    ];

    const displayChips = chips && chips.length > 0 ? chips : defaultChips;

    return (
        <div className="suggestions-grid">
            {displayChips.map((chip, idx) => (
                <button
                    key={idx}
                    className="suggestion-card"
                    onClick={() => onSelect(chip)}
                >
                    {chip}
                </button>
            ))}
        </div>
    );
};
