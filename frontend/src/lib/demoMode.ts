// Hidden demo mode: the backend replays recorded model calls (backend agent/demo.py), so a run takes seconds.
// Toggle: Shift+D (outside text fields), or 5 quick taps in the bottom-left corner (phones), or ?demo=1 / ?demo=0.
// It sets the `pw_demo` cookie, which every request (fetch and EventSource) carries. The only sign is a 4px dot.

const KEY = 'pw_demo'

function isOn(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return document.cookie.includes(`${KEY}=1`) }
}

function apply(on: boolean) {
  try { if (on) localStorage.setItem(KEY, '1'); else localStorage.removeItem(KEY) } catch { /* private mode: cookie only */ }
  document.cookie = on ? `${KEY}=1; path=/; max-age=31536000; SameSite=Lax` : `${KEY}=; path=/; max-age=0; SameSite=Lax`
  let dot = document.getElementById('pw-demo-dot')
  if (on && !dot) {
    dot = document.createElement('div')
    dot.id = 'pw-demo-dot'
    dot.setAttribute('aria-hidden', 'true')
    Object.assign(dot.style, {
      position: 'fixed', left: '6px', bottom: '6px', width: '4px', height: '4px', borderRadius: '50%',
      background: 'currentColor', opacity: '0.35', pointerEvents: 'none', zIndex: '2147483647',
    })
    document.body.appendChild(dot)
  } else if (!on && dot) dot.remove()
}

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null
  return !!el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName))
}

function init() {
  const q = new URLSearchParams(location.search).get('demo')
  apply(q === '1' ? true : q === '0' ? false : isOn())

  window.addEventListener('keydown', (e) => {
    if (e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && e.code === 'KeyD' && !typing(e.target)) apply(!isOn())
  })

  let taps: number[] = []
  document.addEventListener('pointerdown', (e) => {
    if (e.clientX > 48 || e.clientY < window.innerHeight - 48) return // a 48px hot corner, bottom-left
    const now = Date.now()
    taps = [...taps.filter((t) => now - t < 2500), now]
    if (taps.length >= 5) { taps = []; apply(!isOn()) }
  }, true)
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init)
else init()
