import { useState } from 'react'
import { DIRECTIONS, applyDirection } from '../styles/directions'
import { LOGOS } from '../styles/logos'
import { currentLogo } from './Brand'

function setLogo(slug: string) {
  const u = new URL(location.href)
  u.searchParams.set('logo', slug)
  history.replaceState(history.state, '', u)
  window.dispatchEvent(new Event('paidway-logo'))
}

/** Floating picker for style directions and logos; only rendered when ?style= or ?logo= is in the URL. */
export default function StyleSwitcher({ initial }: { initial: string | null }) {
  const [i, setI] = useState(Math.max(0, DIRECTIONS.findIndex((d) => d.slug === initial)))
  const [logo, setL] = useState(currentLogo().slug)
  const pick = (n: number) => {
    const k = (n + DIRECTIONS.length) % DIRECTIONS.length
    setI(k)
    applyDirection(DIRECTIONS[k].slug)
  }
  return (
    <div className="style-switcher" role="group" aria-label="Style direction">
      <button onClick={() => pick(i - 1)} aria-label="Previous style">‹</button>
      <select value={DIRECTIONS[i].slug} onChange={(e) => pick(DIRECTIONS.findIndex((x) => x.slug === e.target.value))} aria-label="Style">
        {DIRECTIONS.map((x, n) => <option key={x.slug} value={x.slug}>{n + 1}. {x.name}</option>)}
      </select>
      <button onClick={() => pick(i + 1)} aria-label="Next style">›</button>
      <select value={logo} onChange={(e) => { setL(e.target.value); setLogo(e.target.value) }} aria-label="Logo">
        {LOGOS.map((x) => <option key={x.slug} value={x.slug}>Logo: {x.name}</option>)}
      </select>
    </div>
  )
}
