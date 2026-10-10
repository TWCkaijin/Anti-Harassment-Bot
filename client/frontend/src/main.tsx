import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { I18nProvider } from './i18n'
import './index.css'
import App from './App.tsx'

// Retire credentials persisted by the former embedded administrator panel.
try { localStorage.removeItem('harassment_bot_admin_token') } catch { /* Storage can be disabled. */ }

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </StrictMode>,
)
