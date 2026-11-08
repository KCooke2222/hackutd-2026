import { useEffect, useState } from 'react'
import { ALL_LOGOS, DEFAULT_LOGO, lockup } from '../styles/logos'

/** Dev-only: ?logo=<slug> previews another lockup; the switcher fires 'paidway-logo' to change it live. */
export function currentLogo() {
  const slug = new URLSearchParams(location.search).get('logo') ?? DEFAULT_LOGO
  return ALL_LOGOS.find((l) => l.slug === slug) ?? ALL_LOGOS.find((l) => l.slug === DEFAULT_LOGO)!
}

/** `onHome` makes a plain click navigate in-app (no reload, so a running plan isn't lost); modified clicks still open `/`. */
export default function Brand({ onHome }: { onHome?: () => void }) {
  const [logo, setLogo] = useState(currentLogo)
  useEffect(() => {
    const on = () => setLogo(currentLogo())
    window.addEventListener('paidway-logo', on)
    return () => window.removeEventListener('paidway-logo', on)
  }, [])
  const click = (e: React.MouseEvent) => {
    if (!onHome || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
    e.preventDefault()
    onHome()
  }
  return <a className="brand" href="/" aria-label="Paidway home" onClick={click} dangerouslySetInnerHTML={{ __html: lockup(logo) }} />
}
