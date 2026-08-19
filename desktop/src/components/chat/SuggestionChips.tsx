export const SuggestionChips = ({ onSelect }: { onSelect: (text: string) => void }) => (
    <div className="suggestions-grid">
        <button className="suggestion-card" onClick={() => onSelect("What is my total revenue?")}>
            What is my total revenue?
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Which medicines are expiring soon?")}>
            Which medicines are expiring soon?
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Show me revenue by month")}>
            Show me revenue by month
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Give me a summary of my pharmacy data")}>
            Give me a summary of my pharmacy data
        </button>
    </div>
);
