import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './styles/directions.css'
import App from './App.tsx'
import StyleSwitcher from './components/StyleSwitcher'
import { applyDirection, styleParam } from './styles/directions'

// Dev-only: ?style=<slug> / ?logo=<slug> preview a style direction or logo with a floating switcher.
const style = styleParam()
const preview = style || new URLSearchParams(location.search).has('logo')
if (style) applyDirection(style)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
    {preview && <StyleSwitcher initial={style} />}
  </StrictMode>,
)
