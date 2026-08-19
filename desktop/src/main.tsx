import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Shell from './Shell'
import { Dashboard, ConnectSource, Chatbot, ReportExport } from './Pages'
import './index.css'

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Shell />}>
          <Route index element={<Dashboard />} />
          <Route path="connect" element={<ConnectSource />} />
          <Route path="chat" element={<Chatbot />} />
          <Route path="reports" element={<ReportExport />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>,
)
