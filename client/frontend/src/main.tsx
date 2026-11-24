import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nProvider, setChatUiAnalytics } from '@rag/shared/chat-ui'
import { trackAnalytics } from './services/analytics'
import './index.css'
import App from './App.tsx'

// Retire credentials persisted by the former embedded administrator panel.
try { localStorage.removeItem('harassment_bot_admin_token') } catch { /* Storage can be disabled. */ }

// Shared chat components report interaction events through the client's consent-gated analytics.
setChatUiAnalytics(trackAnalytics)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
