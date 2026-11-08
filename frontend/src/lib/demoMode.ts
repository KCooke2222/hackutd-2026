// Demo mode: the backend replays recorded model calls (backend agent/demo.py), each at its recorded pace but at
// most 5 s, so a run feels live without waiting on the model. A miss (something typed) calls the model for real.
// Toggle: the pill hidden in the bottom-left corner (hover to reveal), Shift+D (outside text fields), 5 quick taps in the bottom-left corner
// (phones), or ?demo=1 / ?demo=0. It sets the `pw_demo` cookie, which every request (fetch and EventSource) carries.
// While on, the tab reads "demo".

const KEY = 'pw_demo'

export function isOn(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return document.cookie.includes(`${KEY}=1`) }
}

let tab: HTMLButtonElement | null = null

function apply(on: boolean) {
  try { if (on) localStorage.setItem(KEY, '1'); else localStorage.removeItem(KEY) } catch { /* private mode: cookie only */ }
  document.cookie = on ? `${KEY}=1; path=/; max-age=31536000; SameSite=Lax` : `${KEY}=; path=/; max-age=0; SameSite=Lax`
  if (!tab) {
    tab = document.createElement('button')
    tab.type = 'button'
    tab.className = 'pw-demo-tab'
    tab.addEventListener('click', () => apply(!isOn()))
    document.body.appendChild(tab)
  }
  tab.classList.toggle('on', on)
  tab.textContent = on ? 'demo on' : 'demo off'
  tab.title = on ? 'Demo mode on: recorded answers, at most 5 s each. Click to turn off.' : 'Demo mode'
  tab.setAttribute('aria-pressed', String(on))
  tab.setAttribute('aria-label', on ? 'Demo mode on' : 'Demo mode off')
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
    if (e.target === tab) return
    if (e.clientX > 48 || e.clientY < window.innerHeight - 48) return // a 48px hot corner, bottom-left
    const now = Date.now()
    taps = [...taps.filter((t) => now - t < 2500), now]
    if (taps.length >= 5) { taps = []; apply(!isOn()) }
  }, true)
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init)
else init()
