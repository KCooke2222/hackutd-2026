/** Paidway logo marks (24x24 SVG bodies). Colors: var(--accent), var(--on-accent), currentColor.
 *  Dev preview: ?logo=<slug> swaps the header mark. Plain data so the gallery script can import it too. */
export type Logo = { slug: string; name: string; blurb: string; body: string; lower?: boolean }

const A = 'var(--accent)'
const W = 'var(--on-accent)'
const S = (w: number, c = W) => `fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"`

export const LOGOS: Logo[] = [
  { slug: 'step', name: 'Step down', blurb: 'The current mark: a balance stepping down to a stop.',
    body: `<rect width="24" height="24" rx="6.5" fill="${A}"/><path d="M5.5 7H10v5h4v5h3" ${S(2.2)}/><circle cx="18.2" cy="17" r="1.9" fill="${W}"/>` },
  { slug: 'ppath', name: 'P path', blurb: 'A route that draws a P: start dot, up, around.',
    body: `<path d="M7 19.5V4.5h5.6a4.75 4.75 0 0 1 0 9.5H7" ${S(2.8, A)}/><circle cx="7" cy="19.6" r="2.4" fill="${A}"/>` },
  { slug: 'rise', name: 'Rising line', blurb: 'Net worth going up: an arrowed line in a coin.',
    body: `<circle cx="12" cy="12" r="11.5" fill="${A}"/><path d="M6 15.5l3.6-3.6 3 2.6L17.6 9" ${S(2.1)}/><path d="M14.2 9h3.4v3.4" ${S(2.1)}/>` },
  { slug: 'check', name: 'Paid check', blurb: 'A checkmark whose tail climbs out of the tile.',
    body: `<rect width="24" height="24" rx="7" fill="${A}"/><path d="M6.5 12.6l3.6 3.6L17.8 6.5" ${S(2.5)}/><circle cx="17.8" cy="6.5" r="1.6" fill="${W}"/>` },
  { slug: 'coin', name: 'P coin', blurb: 'A coin with an engraved P and rim.',
    body: `<circle cx="12" cy="12" r="11.5" fill="${A}"/><circle cx="12" cy="12" r="8.6" fill="none" stroke="${W}" stroke-opacity=".35" stroke-width="1.2"/><path d="M10 16.6V7.4h3.1a2.7 2.7 0 0 1 0 5.4H10" ${S(2.1)}/>` },
  { slug: 'road', name: 'Road ahead', blurb: 'A road narrowing to the horizon: the way to paid.',
    body: `<rect width="24" height="24" rx="6.5" fill="${A}"/><path d="M6.5 20L10.6 5M17.5 20L13.4 5" ${S(1.9)}/><path d="M12 18.8V7" fill="none" stroke="${W}" stroke-width="1.7" stroke-linecap="round" stroke-dasharray="2.4 2.6"/>` },
  { slug: 'monogram', name: 'pw monogram', blurb: 'Lowercase p and w set as one stroke family.', lower: true,
    body: `<rect width="24" height="24" rx="6.5" fill="currentColor"/><path d="M4.6 8.2V19.5M4.6 8.2h3a3 3 0 0 1 0 6h-3" ${S(1.9, A)}/><path d="M11.9 8.2l1.9 7 2.1-5.4 2.1 5.4 1.9-7" ${S(1.9, A)}/>` },
  { slug: 'tozero', name: 'To zero', blurb: 'A debt line falling until it lands on the zero line.',
    body: `<path d="M4.5 19.5h15" ${S(2.2, 'currentColor')}/><path d="M5.5 5l4.8 5.6 3.2-2.6 4.7 9.4" ${S(2.4, A)}/><circle cx="18.2" cy="19.4" r="2.4" fill="${A}"/>` },
  { slug: 'bars', name: 'Shrinking bars', blurb: 'Balances shrinking month by month, ending at a zero.',
    body: `<rect x="3.5" y="5" width="4.2" height="15" rx="1.4" fill="${A}"/><rect x="9.9" y="10" width="4.2" height="10" rx="1.4" fill="${A}" fill-opacity=".7"/><circle cx="18.4" cy="17.6" r="2.3" fill="none" stroke="${A}" stroke-width="2"/>` },
  { slug: 'waypoint', name: 'Waypoint', blurb: 'A map pin with a check: you are here, and it is paid.',
    body: `<path d="M12 23s-8-6.8-8-13.2a8 8 0 0 1 16 0C20 16.2 12 23 12 23z" fill="${A}"/><path d="M8.6 10.1l2.4 2.4 4.5-4.6" ${S(2.2)}/>` },
]

export function logoSvg(l: Logo, size: number) {
  return `<svg class="mark" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${l.body}</svg>`
}
