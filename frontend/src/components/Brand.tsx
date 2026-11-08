import { useEffect, useState } from 'react'
import { LOGOS } from '../styles/logos'

/** Dev-only: ?logo=<slug> previews another mark; the switcher fires 'paidway-logo' to change it live. */
export function currentLogo() {
  const slug = new URLSearchParams(location.search).get('logo')
  return LOGOS.find((l) => l.slug === slug) ?? LOGOS[0]
}

export function Mark({ size = 22, slug }: { size?: number; slug?: string }) {
  const l = LOGOS.find((x) => x.slug === slug) ?? LOGOS[0]
  return <svg className="mark" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" dangerouslySetInnerHTML={{ __html: l.body }} />
}

export default function Brand() {
  const [logo, setLogo] = useState(currentLogo)
  useEffect(() => {
    const on = () => setLogo(currentLogo())
    window.addEventListener('paidway-logo', on)
    return () => window.removeEventListener('paidway-logo', on)
  }, [])
  return (
    <a className="brand" href="/" aria-label="Paidway home">
      <Mark slug={logo.slug} />
      <span>{logo.lower ? 'paidway' : 'Paidway'}</span>
    </a>
  )
}
