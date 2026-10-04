# Test Report 05: Chat Panel, Report Export & Background Service Monitoring

## Executive Summary
This report validates the conversational AI interface (`Chatbot.tsx`), report export workflows (`WeeklyReport.tsx`), and real-time backend background service health monitoring (`TopBar.tsx`) in Module 6.9.

The desktop chat interface allows non-technical business operators to query their ingested financial ledger in natural language. Streaming responses, deterministic tabular data formatting, and source citation provenance chips are all rendered within a native desktop shell.

---

## Test Cases & Specifications

| Test Case | Target Component | Validated User Experience | Status |
| :--- | :--- | :--- | :---: |
| `test_chatbot_page_structure` | `Chatbot.tsx` | Verifies integration of message history list, multi-turn state, and composer input. | **PASS** |
| `test_composer_input_and_submit_controls` | `Composer.tsx` | Verifies text query input, keyboard shortcut handling (Enter/Shift+Enter), and source scope toggles. | **PASS** |
| `test_message_bubble_citations_and_tables` | `MessageBubble.tsx` | Confirms rendering of structured markdown tables and interactive citation provenance chips. | **PASS** |
| `test_typing_indicator_component` | `TypingIndicator.tsx` | Confirms animated pulsing dots indicating local model inference activity. | **PASS** |
| `test_weekly_report_export_controls` | `WeeklyReport.tsx` | Verifies one-click export buttons generating deterministic PDF and Excel financial reports. | **PASS** |
| `test_topbar_background_service_health_indicator` | `TopBar.tsx` | Verifies real-time monitoring of local background service health and active domain status. | **PASS** |

---

## Operational Architecture
1. **Background Service Bundling**:
   - The desktop shell operates against the local backend service (`http://127.0.0.1:8000`).
   - `TopBar.tsx` periodically checks health and displays an online status indicator so the user is immediately aware of engine readiness.
2. **Deterministic Table & Citation Display**:
   - Financial figures returned by the RAG engine are formatted in high-contrast tabular cards.
   - Every claim is accompanied by citation badges referencing the physical file and row from which the insight was extracted.
3. **Report Export Workflows**:
   - Business users can generate formal, audit-ready weekly and monthly reports with charts, executive commentary, and provenance appendices without opening a spreadsheet editor or terminal.
