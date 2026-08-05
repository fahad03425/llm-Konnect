# Module 6.5 — RAG Chatbot

This module implements the chatbot interface for LLM-Konnect, following a strict "Code computes, LLM narrates" philosophy.

## 1. Routing Strategy (Hallucination Prevention)

To guarantee that the local LLM never invents numbers, every question is routed deterministically:

1. **Analytics Route** (Keywords: total, sum, average, kitna, profit, margin, expiring)
   - Bypasses LLM math. 
   - Uses `AnalyticsRouter` to compute the aggregate over the canonical DataFrame deterministically.
   - The computed numbers are injected into the LLM's prompt as unalterable facts, and the LLM merely translates them into natural language.

2. **RAG Route** (Lookup questions: "show me", "which", "when")
   - Uses `KnowledgeBase` to retrieve the top matching records via semantic search.
   - Constrains the LLM system prompt to answer *only* from the provided context.
   - Short-circuits with a canned response if no records are found to save VRAM and latency.

3. **Chit-chat Route** (Keywords: hello, hi, what can you do)
   - Skips retrieval and uses a fast, static capability description.

## 2. 4GB VRAM Optimization

The target machine is constrained to 4GB VRAM. This module implements several strict memory and latency controls:
- **`llm_keep_alive_chat="5m"`**: The model remains loaded in VRAM between chat turns. Unloading and reloading the model takes several seconds, which destroys chat interactivity.
- **`llm_num_predict=256`**: Caps the model's output generation length to prevent run-on text and control latency.
- **`retrieval_top_k=5`**: Keeps the context window tiny. The KV cache grows linearly with context size and consumes VRAM.
- **Streaming Output**: Tokens are streamed back to the client immediately to lower perceived latency.
- **Bounded History**: `llm_chat_history_size=3` ensures only the last 3 conversational turns are fed into the context, preventing context bloat.

## 3. Grounding and Citations

Every RAG answer is accompanied by structured source references. The system tracks the exact `source_row` index from the original dataset and surfaces it alongside a descriptive label (e.g., "Invoice 1011") so the user can audit the claim.

## 4. Domain Agnosticism

The chatbot core contains no pharmacy-specific vocabulary. All domain rules (like recognizing the word "panadol" or knowing to extract specific filters) are mediated through the injected `DomainPack` (e.g., `PharmacyDomainPack`), making the chatbot immediately compatible with grocery or other domains.
