# Module 6.9: Desktop Application Module — File Catalog & Architecture

## Overview
Module 6.9 provides the user-facing desktop shell for the LLM-Konnect Financial Intelligence Suite. Built with **Tauri v2**, **React 19**, **TypeScript**, and **Vite**, it bundles the local backend engine as an invisible background service. 

It covers first-time onboarding, automated model selection, data source connections (CSV/Excel, Tally Prime, Shopify), an interactive KPI and charts dashboard, an offline RAG chat interface, and deterministic PDF/Excel report exports—ensuring a non-technical business owner never has to see a command line.

---

## Identified Files of Module 6.9

### 1. Tauri Backend & Packaging Configuration
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src-tauri/tauri.conf.json`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src-tauri/tauri.conf.json) | Desktop Window Configuration | Window specs (1280x800, min 900x600, resizable), frameless styling (`decorations: false`), dev/build hooks, security CSP, and multi-platform packaging bundle targets. |
| [`desktop/src-tauri/Cargo.toml`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src-tauri/Cargo.toml) | Rust Core Manifest | Tauri v2 dependencies (`tauri`, `tauri-plugin-log`). |
| [`desktop/src-tauri/src/main.rs`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src-tauri/src/main.rs) | Rust Entry Point | Invokes `desktop_lib::run()`. |
| [`desktop/src-tauri/src/lib.rs`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src-tauri/src/lib.rs) | Rust App Builder | Initializes logging plugins and Tauri application context. |
| [`desktop/package.json`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/package.json) | Node.js Manifest | Frontend dependencies: React 19, `@tauri-apps/api`, `react-router-dom`, `recharts`, `lucide-react`, and Vite. |
| [`desktop/vite.config.ts`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/vite.config.ts) | Vite Bundler Config | React plugin, dev server port (5173), and production output directory (`../dist`). |

### 2. Desktop Shell & Navigation Framework
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src/main.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/main.tsx) | Application Entry Point | `GlobalErrorBoundary` UI fallback, context providers (`UserProvider`, `FileProvider`, `ChatProvider`, `ReportProvider`), and React Router hierarchy. |
| [`desktop/src/Shell.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/Shell.tsx) | Master Layout | Hosts the persistent layout: `Sidebar`, `TopBar`, `WindowTitleBar`, and dynamic page outlet. |
| [`desktop/src/components/WindowTitleBar.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/WindowTitleBar.tsx) | Custom Window Controls | Frameless window title bar with drag region, minimize, maximize/restore, and window close controls via Tauri IPC (`getCurrentWindow()`). |
| [`desktop/src/components/TopBar.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/TopBar.tsx) | Service Status Bar | Real-time backend connectivity status indicator, active business domain badge, sync status, and quick settings trigger. |
| [`desktop/src/components/Sidebar.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/Sidebar.tsx) | Desktop Sidebar | Primary navigation across Dashboard, Connect Data, Uploaded Files, Chatbot, and Weekly Reports. |

### 3. Onboarding & Model Management
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src/components/auth/OnboardingModal.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/auth/OnboardingModal.tsx) | Initial Setup Wizard | Welcomes non-technical users, guides business domain selection (Pharmacy, E-Commerce, General), detects hardware, and initializes the local profile. |
| [`desktop/src/components/settings/ModelSettings.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/settings/ModelSettings.tsx) | Model Manager | Hardware-recommended model selection (Phi-4-mini for CPU-only, Qwen 2.5 / Mistral for GPU), Ollama status, and one-click model switching without CLI. |
| [`desktop/src/components/settings/SettingsModal.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/settings/SettingsModal.tsx) | Desktop Settings | Global preferences modal (domain switching, model profiles, currency symbol selection). |

### 4. Data Source Connection & Ingestion Wizard
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src/pages/ConnectSource.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/ConnectSource.tsx) | Ingestion Wizard | 4-step connector workflow: Universal CSV/Excel, Tally Prime ODBC/HTTP connector, Shopify Admin API token connection. |
| [`desktop/src/components/connect/UploadZone.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/connect/UploadZone.tsx) | Drag-and-Drop Zone | Local file picker and drop zone supporting `.csv`, `.xlsx`, `.xls`, `.xml`. |
| [`desktop/src/components/connect/PreviewTable.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/connect/PreviewTable.tsx) | Source Data Preview | Renders first 5 sample rows of raw extracted data. |
| [`desktop/src/components/connect/MappingTable.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/connect/MappingTable.tsx) | Schema Verification | UI for user-confirmed mapping of source headers into standard canonical fields. |
| [`desktop/src/components/connect/KBStatus.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/connect/KBStatus.tsx) | Knowledge Base Status | Displays progress for offline chunking, embedding generation, and Chroma vector indexing. |
| [`desktop/src/pages/UploadedFiles.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/UploadedFiles.tsx) | File Catalog | Lists registered files, ingested chunk counts, database connection sync status, and deletion controls. |

### 5. Financial Dashboard & Charts
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src/pages/Dashboard.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/Dashboard.tsx) | Executive Dashboard | KPI metrics cards (Revenue, Gross Margin %, Net Profit, Units Sold, Refund Rate %, ATV, Anomalies), Recharts time-series, Top-5 Products, date presets ('7d', '28d', '6m', 'all'), and provenance audit tooltips. |
| [`desktop/src/pages/Dashboard.css`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/Dashboard.css) | Dashboard Styling | Responsive CSS grid, dark-mode glassmorphism cards, and interactive hover states. |

### 6. RAG Chatbot & Report Export
| File Path | Role | Key Invariants & Responsibilities |
| :--- | :--- | :--- |
| [`desktop/src/pages/Chatbot.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/Chatbot.tsx) | RAG Conversational UI | Multi-turn chat session with streaming responses, mode switching, and active document scoping. |
| [`desktop/src/components/chat/Composer.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/chat/Composer.tsx) | Message Input | Multi-line composer with submit triggers, mode badges, and source attachments. |
| [`desktop/src/components/chat/MessageBubble.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/components/chat/MessageBubble.tsx) | Message Renderer | Markdown parsing, deterministic tabular data formatting, and source citation provenance chips. |
| [`desktop/src/pages/WeeklyReport.tsx`](file:///c:/Users/User/Downloads/llm-Konnect-3/llm-Konnect/desktop/src/pages/WeeklyReport.tsx) | Report Generator | Comprehensive weekly/monthly executive report export to PDF and Excel with mathematical audit notes. |
