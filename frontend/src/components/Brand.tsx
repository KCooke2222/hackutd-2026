import { useEffect, useState } from 'react'
import { ALL_LOGOS, DEFAULT_LOGO, lockup } from '../styles/logos'

/** Dev-only: ?logo=<slug> previews another lockup; the switcher fires 'paidway-logo' to change it live. */
export function currentLogo() {
  const slug = new URLSearchParams(location.search).get('logo') ?? DEFAULT_LOGO
  return ALL_LOGOS.find((l) => l.slug === slug) ?? ALL_LOGOS.find((l) => l.slug === DEFAULT_LOGO)!
}

export default function Brand() {
  const [logo, setLogo] = useState(currentLogo)
  useEffect(() => {
    const on = () => setLogo(currentLogo())
    window.addEventListener('paidway-logo', on)
    return () => window.removeEventListener('paidway-logo', on)
  }, [])
  return <a className="brand" href="/" aria-label="Paidway home" dangerouslySetInnerHTML={{ __html: lockup(logo) }} />
}
