/** Paidway logo options. Dev preview: ?logo=<slug> swaps the header lockup, ?logotone=style recolours it with the
 *  direction's accent. Marks are 24x24 SVG bodies coloured with var(--lc) / var(--lc-on) (set per tone in logos.css).
 *  Plain data so the gallery script can import it too. */
export type Logo = {
  slug: string; name: string; kind: string; blurb: string
  tone: 'green' | 'deep' | 'teal' | 'navy' | 'ink' | 'accent'
  mark?: string        // 24x24 SVG body
  word?: string        // wordmark HTML (default "Paidway")
  bare?: boolean       // lockup is the wordmark alone (mark, if any, is the app icon)
  legacy?: boolean
}

const C = 'var(--lc)'
const O = 'var(--lc-on)'
const S = (w: number, c = O) => `fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"`

export const LOGO_FONTS = 'https://fonts.googleapis.com/css2?display=swap&family=Outfit:wght@300;600;800&family=Archivo+Narrow:wght@700&family=Archivo:wght@600'

export const LOGOS: Logo[] = [
  { slug: 'stamp', name: 'PAID stamp', kind: 'Stamp wordmark', tone: 'green', bare: true,
    blurb: 'The word reads as a rubber stamp: PAID, inked, then "way".',
    word: '<span class="st">PAID</span>way',
    mark: `<g transform="rotate(-8 12 12)"><rect x="2.6" y="5.2" width="18.8" height="13.6" rx="2" fill="none" stroke="${C}" stroke-width="2"/><rect x="5" y="7.6" width="14" height="8.8" rx=".8" fill="none" stroke="${C}" stroke-width=".9"/></g><path d="M8.6 12.3l2.3 2.3 4.6-4.7" ${S(2.1, C)}/>` },
  { slug: 'paidbold', name: 'Paid + way', kind: 'Wordmark', tone: 'green', bare: true,
    blurb: 'Pure type: heavy green "Paid", light "way". The name does the work.',
    word: '<b>Paid</b><i>way</i>' },
  { slug: 'ytail', name: 'Way underline', kind: 'Wordmark', tone: 'teal', bare: true,
    blurb: 'The y drops into a line that runs back under the word: the way, from start dot to paid.',
    word: 'Paidwa<span class="y">y</span>' },
  { slug: 'checkdot', name: 'Check dot', kind: 'Wordmark', tone: 'deep', bare: true,
    blurb: 'Lowercase, with the i dotted by a check. Quiet, friendly, very ownable.',
    word: 'pa<span class="i">ı<svg viewBox="0 0 10 8" aria-hidden="true"><path d="M1.3 4.3l2.4 2.3L8.7 1.4"/></svg></span>dway' },
  { slug: 'proad', name: 'P road', kind: 'Monogram', tone: 'green',
    blurb: 'A P built as a road, centre line and all: the way, in the initial.',
    mark: `<path d="M6.9 22V5.2h5.6a5 5 0 0 1 0 10H6.9" fill="none" stroke="${C}" stroke-width="5" stroke-linejoin="round"/><path d="M6.9 21.5V5.2h5.6a5 5 0 0 1 0 10H6.9" fill="none" stroke="${O}" stroke-width="1" stroke-dasharray="1.7 1.7"/>` },
  { slug: 'pw', name: 'pw ligature', kind: 'Monogram', tone: 'ink',
    blurb: 'Geometric p and w sharing one stroke: the bowl of the p is the first leg of the w.',
    mark: `<path d="M4.5 20V5.5h5.9L12.6 15 15 8.3l2.4 6.7 2.4-9.5" ${S(2.3, C)}/><path d="M12.6 15H4.5" ${S(2.3, C)}/>` },
  { slug: 'finish', name: 'Switchback', kind: 'Line mark', tone: 'teal',
    blurb: 'A switchback road from a start dot up to a finish flag.',
    mark: `<path d="M4.2 20.4H11a3.2 3.2 0 0 0 0-6.4H8.6a3.2 3.2 0 0 1 0-6.4h9.6" ${S(2.2, C)}/><circle cx="4.2" cy="20.4" r="2" fill="${C}"/><path d="M19 8.2V1.8" ${S(1.6, C)}/><path d="M19 1.8h3.6l-1 1.8 1 1.8H19z" fill="${C}"/>` },
  { slug: 'wzero', name: 'W to zero', kind: 'Line mark', tone: 'navy',
    blurb: 'A W whose peaks keep dropping until the line settles on zero.',
    mark: `<path d="M2.6 3.8l4 15.2 3.8-9.4L14 19l3-4.8 2.4 4.8h2" ${S(2.2, C)}/>` },
  { slug: 'signpost', name: 'Signpost', kind: 'Emblem', tone: 'deep',
    blurb: 'A fingerpost pointing the way: one sign filled, one outlined.',
    mark: `<rect x="10.8" y="2.5" width="2.4" height="20" rx="1" fill="${C}"/><path d="M4.5 4.6h12.2l3.2 3.2-3.2 3.2H4.5z" fill="${C}"/><path d="M19.2 13.4H8.4l-2.7 2.7 2.7 2.7h10.8z" fill="none" stroke="${C}" stroke-width="1.6" stroke-linejoin="round"/>` },
  { slug: 'milestone', name: 'Mile zero', kind: 'Badge', tone: 'navy',
    blurb: 'A roadside milestone marked 0: the balance at the end of the road.',
    mark: `<path d="M5.5 20.5V10a6.5 6.5 0 0 1 13 0v10.5z" fill="${C}"/><ellipse cx="12" cy="12.6" rx="2.5" ry="3.5" fill="none" stroke="${O}" stroke-width="2"/><path d="M3.2 21.6h17.6" ${S(1.8, C)}/>` },
  { slug: 'receipt', name: 'Receipt', kind: 'Geometric', tone: 'teal',
    blurb: 'A torn-edge receipt, ticked off. Literal, but clearly money and clearly done.',
    mark: `<path d="M5.5 2.5h13v19l-2.17-1.6-2.16 1.6-2.17-1.6-2.17 1.6-2.16-1.6-2.17 1.6z" fill="${C}"/><path d="M8.6 6.4h6.8M8.6 9.4h4.2" ${S(1.6)}/><path d="M8.7 14.1l2.1 2.1 4.5-4.5" ${S(2)}/>` },
  { slug: 'tag', name: 'Paid tag', kind: 'Badge', tone: 'deep',
    blurb: 'A price tag with a check: the bill, settled.',
    mark: `<path d="M12.6 2.5H20a1.5 1.5 0 0 1 1.5 1.5v7.4a1.6 1.6 0 0 1-.47 1.13l-8.6 8.6a1.6 1.6 0 0 1-2.26 0l-6.8-6.8a1.6 1.6 0 0 1 0-2.26l8.6-8.6A1.6 1.6 0 0 1 12.6 2.5z" fill="${C}"/><circle cx="17.3" cy="6.7" r="1.6" fill="${O}"/><path d="M7.9 12.9l2.4 2.4 4.4-4.4" ${S(2)}/>` },
]

/** The first-round marks (accent tiles). Kept for comparison; `step` is still the default header mark. */
const A = 'var(--accent)'
const W = 'var(--on-accent)'
const T = (w: number, c = W) => `fill="none" stroke="${c}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"`
const old = (slug: string, name: string, mark: string, word = 'Paidway'): Logo =>
  ({ slug, name, kind: 'Round 1', blurb: '', tone: 'accent', legacy: true, mark, word })

export const LEGACY_LOGOS: Logo[] = [
  old('step', 'Step down', `<rect width="24" height="24" rx="6.5" fill="${A}"/><path d="M5.5 7H10v5h4v5h3" ${T(2.2)}/><circle cx="18.2" cy="17" r="1.9" fill="${W}"/>`),
  old('ppath', 'P path', `<path d="M7 19.5V4.5h5.6a4.75 4.75 0 0 1 0 9.5H7" ${T(2.8, A)}/><circle cx="7" cy="19.6" r="2.4" fill="${A}"/>`),
  old('rise', 'Rising line', `<circle cx="12" cy="12" r="11.5" fill="${A}"/><path d="M6 15.5l3.6-3.6 3 2.6L17.6 9" ${T(2.1)}/><path d="M14.2 9h3.4v3.4" ${T(2.1)}/>`),
  old('check', 'Paid check', `<rect width="24" height="24" rx="7" fill="${A}"/><path d="M6.5 12.6l3.6 3.6L17.8 6.5" ${T(2.5)}/><circle cx="17.8" cy="6.5" r="1.6" fill="${W}"/>`),
  old('coin', 'P coin', `<circle cx="12" cy="12" r="11.5" fill="${A}"/><circle cx="12" cy="12" r="8.6" fill="none" stroke="${W}" stroke-opacity=".35" stroke-width="1.2"/><path d="M10 16.6V7.4h3.1a2.7 2.7 0 0 1 0 5.4H10" ${T(2.1)}/>`),
  old('road', 'Road ahead', `<rect width="24" height="24" rx="6.5" fill="${A}"/><path d="M6.5 20L10.6 5M17.5 20L13.4 5" ${T(1.9)}/><path d="M12 18.8V7" fill="none" stroke="${W}" stroke-width="1.7" stroke-linecap="round" stroke-dasharray="2.4 2.6"/>`),
  old('monogram', 'pw monogram', `<rect width="24" height="24" rx="6.5" fill="currentColor"/><path d="M4.6 8.2V19.5M4.6 8.2h3a3 3 0 0 1 0 6h-3" ${T(1.9, A)}/><path d="M11.9 8.2l1.9 7 2.1-5.4 2.1 5.4 1.9-7" ${T(1.9, A)}/>`, 'paidway'),
  old('tozero', 'To zero', `<path d="M4.5 19.5h15" ${T(2.2, 'currentColor')}/><path d="M5.5 5l4.8 5.6 3.2-2.6 4.7 9.4" ${T(2.4, A)}/><circle cx="18.2" cy="19.4" r="2.4" fill="${A}"/>`),
  old('bars', 'Shrinking bars', `<rect x="3.5" y="5" width="4.2" height="15" rx="1.4" fill="${A}"/><rect x="9.9" y="10" width="4.2" height="10" rx="1.4" fill="${A}" fill-opacity=".7"/><circle cx="18.4" cy="17.6" r="2.3" fill="none" stroke="${A}" stroke-width="2"/>`),
  old('waypoint', 'Waypoint', `<path d="M12 23s-8-6.8-8-13.2a8 8 0 0 1 16 0C20 16.2 12 23 12 23z" fill="${A}"/><path d="M8.6 10.1l2.4 2.4 4.5-4.6" ${T(2.2)}/>`),
]

export const ALL_LOGOS = [...LOGOS, ...LEGACY_LOGOS]
export const DEFAULT_LOGO = 'step'

export function markSvg(l: Logo, size = '1.25em') {
  return l.mark ? `<svg class="mark" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${l.mark}</svg>` : ''
}

/** Header lockup HTML: mark + wordmark, sized in em so it scales with font-size. */
export function lockup(l: Logo) {
  return `<span class="lg lg-${l.slug} tone-${l.tone}${l.legacy ? ' lg-legacy' : ''}">${l.bare ? '' : markSvg(l)}<span class="w">${l.word ?? 'Paidway'}</span></span>`
}
