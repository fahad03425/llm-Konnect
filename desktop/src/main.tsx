import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Shell from './Shell'
import Dashboard from './pages/Dashboard'
import ConnectSource from './pages/ConnectSource'
import Chatbot from './pages/Chatbot'
import WeeklyReport from './pages/WeeklyReport'
import UploadedFiles from './pages/UploadedFiles'
import { FileProvider } from './context/FileContext'
import { UserProvider } from './context/UserContext'
import { ReportProvider } from './context/ReportContext'
import { ChatProvider } from './context/ChatContext'
import './index.css'

class GlobalErrorBoundary extends React.Component<{ children: React.ReactNode }, { hasError: boolean; error: Error | null }> {
  constructor(props: { children: React.ReactNode }) {
    super(props);
    this.state = { hasError: false, error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { hasError: true, error };
  }
  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: '#0F172A',
          color: '#F8FAFC',
          fontFamily: 'Inter, system-ui, -apple-system, sans-serif',
          padding: '2rem'
        }}>
          <div style={{
            maxWidth: '540px',
            width: '100%',
            background: '#1E293B',
            borderRadius: '16px',
            border: '1px solid #334155',
            padding: '2.5rem',
            boxShadow: '0 20px 25px -5px rgba(0, 0, 0, 0.5)'
          }}>
            <div style={{ fontSize: '2rem', marginBottom: '1rem' }}>⚠️</div>
            <h2 style={{ fontSize: '1.4rem', fontWeight: 700, margin: '0 0 0.5rem 0', color: '#F1F5F9' }}>
              KonnectAI Application Notice
            </h2>
            <p style={{ color: '#94A3B8', fontSize: '0.9rem', lineHeight: '1.5', margin: '0 0 1.25rem 0' }}>
              The application encountered an unexpected interface error. Your data and knowledge base remain safe.
            </p>
            <div style={{
              background: '#0F172A',
              padding: '0.85rem 1rem',
              borderRadius: '8px',
              border: '1px solid #334155',
              fontFamily: 'monospace',
              fontSize: '0.8rem',
              color: '#F87171',
              wordBreak: 'break-word',
              marginBottom: '1.75rem'
            }}>
              {this.state.error?.message || 'Unknown runtime error'}
            </div>
            <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
              <button
                onClick={() => {
                  try {
                    sessionStorage.clear();
                  } catch {}
                  window.location.href = '/';
                }}
                style={{
                  background: '#0D7377',
                  color: '#FFFFFF',
                  border: 'none',
                  borderRadius: '8px',
                  padding: '0.65rem 1.25rem',
                  fontSize: '0.88rem',
                  fontWeight: 600,
                  cursor: 'pointer'
                }}
              >
                Clear Wizard Cache &amp; Home
              </button>
              <button
                onClick={() => window.location.reload()}
                style={{
                  background: 'transparent',
                  color: '#94A3B8',
                  border: '1px solid #475569',
                  borderRadius: '8px',
                  padding: '0.65rem 1.25rem',
                  fontSize: '0.88rem',
                  fontWeight: 500,
                  cursor: 'pointer'
                }}
              >
                Reload Window
              </button>
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <GlobalErrorBoundary>
      <UserProvider>
        <FileProvider>
          <ChatProvider>
            <ReportProvider>
              <BrowserRouter>
                <Routes>
                  <Route path="/" element={<Shell />}>
                    <Route index element={<Dashboard />} />
                    <Route path="connect" element={<ConnectSource />} />
                    <Route path="files" element={<UploadedFiles />} />
                    <Route path="chat" element={<Chatbot />} />
                    <Route path="weekly-report" element={<WeeklyReport />} />
                  </Route>
                </Routes>
              </BrowserRouter>
            </ReportProvider>
          </ChatProvider>
        </FileProvider>
      </UserProvider>
    </GlobalErrorBoundary>
  </React.StrictMode>,
)
