import { useEffect, useState } from 'react'
import Home from './pages/Home'
import Landing from './pages/Landing'

// `/` with no query is the welcome page; any query (?customer=, ?plan=, ?replay=, ...) is the app.
const inApp = () => location.search.length > 1

export default function App() {
  const [app, setApp] = useState(inApp)
  useEffect(() => {
    const on = () => setApp(inApp())
    window.addEventListener('popstate', on)
    return () => window.removeEventListener('popstate', on)
  }, [])
  if (!app) {
    return <Landing start={(cid) => { history.pushState(null, '', `/?customer=${cid}`); window.scrollTo(0, 0); setApp(true) }} />
  }
  return <Home />
}
