import { useState } from 'react'
import { DIRECTIONS, applyDirection } from '../styles/directions'
import { LEGACY_LOGOS, LOGOS } from '../styles/logos'
import { currentLogo } from './Brand'

function setParam(k: string, v: string | null) {
  const u = new URL(location.href)
  if (v == null) u.searchParams.delete(k)
  else u.searchParams.set(k, v)
  history.replaceState(history.state, '', u)
}

/** Floating picker for style directions and logos; only rendered when ?style= or ?logo= is in the URL. */
export default function StyleSwitcher({ initial }: { initial: string | null }) {
  const [i, setI] = useState(Math.max(0, DIRECTIONS.findIndex((d) => d.slug === initial)))
  const [logo, setL] = useState(currentLogo().slug)
  const [tone, setTone] = useState(document.documentElement.dataset.logotone === 'style')
  const pick = (n: number) => {
    const k = (n + DIRECTIONS.length) % DIRECTIONS.length
    setI(k)
    applyDirection(DIRECTIONS[k].slug)
  }
  const pickLogo = (slug: string) => {
    setL(slug)
    setParam('logo', slug)
    window.dispatchEvent(new Event('paidway-logo'))
  }
  const toggleTone = () => {
    const on = !tone
    setTone(on)
    if (on) document.documentElement.dataset.logotone = 'style'
    else delete document.documentElement.dataset.logotone
    setParam('logotone', on ? 'style' : null)
  }
  return (
    <div className="style-switcher" role="group" aria-label="Style direction">
      <button onClick={() => pick(i - 1)} aria-label="Previous style">‹</button>
      <select value={DIRECTIONS[i].slug} onChange={(e) => pick(DIRECTIONS.findIndex((x) => x.slug === e.target.value))} aria-label="Style">
        {DIRECTIONS.map((x, n) => <option key={x.slug} value={x.slug}>{n + 1}. {x.name}</option>)}
      </select>
      <button onClick={() => pick(i + 1)} aria-label="Next style">›</button>
      <select value={logo} onChange={(e) => pickLogo(e.target.value)} aria-label="Logo">
        <optgroup label="Logos">{LOGOS.map((x) => <option key={x.slug} value={x.slug}>Logo: {x.name}</option>)}</optgroup>
        <optgroup label="Round 1">{LEGACY_LOGOS.map((x) => <option key={x.slug} value={x.slug}>Old: {x.name}</option>)}</optgroup>
      </select>
      <button onClick={toggleTone} aria-pressed={tone} title="Logo colour: its own, or the style's accent">{tone ? 'Accent' : 'Own'}</button>
    </div>
  )
}
