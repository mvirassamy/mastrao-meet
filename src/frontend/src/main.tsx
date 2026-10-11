import React from 'react'
import ReactDOM from 'react-dom/client'

const RECORDING_TEMPLATE_PATH = '/recording-template'

async function renderApplication() {
  const root = ReactDOM.createRoot(document.getElementById('root')!)

  if (window.location.pathname === RECORDING_TEMPLATE_PATH) {
    const { default: RecordingTemplateApp } =
      await import('./features/recordingTemplate/RecordingTemplateApp')
    root.render(<RecordingTemplateApp />)
    return
  }

  const { default: App } = await import('./App.tsx')
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  )
}

void renderApplication()
