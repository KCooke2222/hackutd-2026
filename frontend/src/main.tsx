import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './styles/directions.css'
import './styles/logos.css'
import App from './App.tsx'
import './lib/demoMode' // hidden demo-mode toggle (Shift+D); see the file
import StyleSwitcher from './components/StyleSwitcher'
import { applyDirection, styleParam } from './styles/directions'
import { LOGO_FONTS } from './styles/logos'

// Dev-only: ?style=<slug> / ?logo=<slug> preview a style direction or logo with a floating switcher.
const style = styleParam()
const q = new URLSearchParams(location.search)
const preview = style || q.has('logo')
if (style) applyDirection(style)
else document.documentElement.dataset.direction = 'mac-light' // the chosen default look
if (preview) {
  document.head.insertAdjacentHTML('beforeend', `<link rel="stylesheet" href="${LOGO_FONTS}">`)
  if (q.get('logotone') === 'style') document.documentElement.dataset.logotone = 'style'
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    {preview && <StyleSwitcher initial={style} />}
  </StrictMode>,
)
