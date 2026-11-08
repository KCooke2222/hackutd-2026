/** Dev-only style directions: `?style=<slug>` sets html[data-direction] (tokens live in directions.css). */
export type Direction = { slug: string; name: string; blurb: string; fonts?: string }

const G = 'https://fonts.googleapis.com/css2?display=swap&family='

export const DIRECTIONS: Direction[] = [
  { slug: 'warm', name: 'Paidway warm', blurb: 'The current restyle: warm neutrals, one vermilion accent, Fraunces numbers.' },
  { slug: 'swiss', name: 'Swiss mono', blurb: 'Black, white and one red. Square corners, hairline rules, Inter Tight caps.',
    fonts: G + 'Inter+Tight:wght@400;500;600;700;800' },
  { slug: 'navy', name: 'Fintech navy', blurb: 'Banking-app trust: navy header bar, cool greys, electric blue, Manrope.',
    fonts: G + 'Manrope:wght@400;500;600;700;800' },
  { slug: 'pastel', name: 'Soft pastel cards', blurb: 'Airy lavender canvas, borderless floating cards, violet accent, Plus Jakarta.',
    fonts: G + 'Plus+Jakarta+Sans:wght@400;500;600;700;800' },
  { slug: 'terminal', name: 'Terminal green', blurb: 'Dark-first, monospace numbers, phosphor green on near-black. Dense.',
    fonts: G + 'JetBrains+Mono:wght@400;500;600;700&family=IBM+Plex+Sans:wght@400;500;600' },
  { slug: 'editorial', name: 'Editorial serif', blurb: 'A financial-paper page: big Newsreader headlines, rules instead of boxes.',
    fonts: G + 'Newsreader:opsz,wght@6..72,500;6..72,600&family=Source+Sans+3:wght@400;500;600;700' },
  { slug: 'glass', name: 'Glassy gradient', blurb: 'Frosted cards over a soft colour mesh, gradient buttons, Sora.',
    fonts: G + 'Sora:wght@400;500;600;700' },
  { slug: 'brutal', name: 'Brutalist-lite', blurb: 'Thick black borders, hard offset shadows, cobalt + yellow, Space Grotesk.',
    fonts: G + 'Space+Grotesk:wght@400;500;600;700' },
  { slug: 'sage', name: 'Calm sage', blurb: 'Quiet greens and clay, rounded and roomy, Figtree with Lora numbers.',
    fonts: G + 'Figtree:wght@400;500;600;700&family=Lora:wght@500;600' },
  { slug: 'apple', name: 'Neutral (Apple-like)', blurb: 'Grey canvas, white borderless cards, segmented steps, system blue.',
    fonts: G + 'Inter:wght@400;500;600;700' },
]

export function styleParam(): string | null {
  return new URLSearchParams(location.search).get('style')
}

export function applyDirection(slug: string) {
  const d = DIRECTIONS.find((x) => x.slug === slug) ?? DIRECTIONS[0]
  document.documentElement.dataset.direction = d.slug
  let link = document.getElementById('direction-fonts') as HTMLLinkElement | null
  if (d.fonts) {
    if (!link) {
      link = document.createElement('link')
      link.id = 'direction-fonts'
      link.rel = 'stylesheet'
      document.head.appendChild(link)
    }
    link.href = d.fonts
  } else link?.remove()
  const u = new URL(location.href)
  u.searchParams.set('style', d.slug)
  history.replaceState(history.state, '', u)
}
