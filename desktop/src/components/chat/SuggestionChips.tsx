export const SuggestionChips = ({ onSelect }: { onSelect: (text: string) => void }) => (
    <div className="suggestions-grid">
        <button className="suggestion-card" onClick={() => onSelect("What is my total revenue?")}>
            What is my total revenue?
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Which medicines expire soon?")}>
            Which medicines expire soon?
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Tell me about Brufen in my data")}>
            Tell me about Brufen in my data
        </button>
        <button className="suggestion-card" onClick={() => onSelect("Which supplier has the most invoices?")}>
            Which supplier has the most invoices?
        </button>
    </div>
);
