import { useEffect, useRef, useState } from 'react'
import { ArrowRight, MessageCircle, X } from 'lucide-react'
import { api } from '../../lib/api'
import type { Proposal } from '../../lib/api'
import Chat from './Chat'

type Item = { kind: 'qa'; q: string; a: string | null; err?: string } | { kind: 'updated'; p: Proposal; changes: string[] }

/** App-wide chat: a floating button on every screen. Questions get answers (nothing changes); with a pending
 *  plan, a change re-plans it and links back to the Plan screen. */
export default function ChatDrawer({ cid, ai, onAdjusted, onRules, onSeePlan }: {
  cid: string
  ai: Proposal | null // the plan being looked at (only a pending one can be changed)
  onAdjusted: (p: Proposal) => void
  onRules: (p: Proposal) => void
  onSeePlan: (p: Proposal) => void
}) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Item[]>([])
  const [asking, setAsking] = useState(false)
  const end = useRef<HTMLDivElement | null>(null)
  const pending = ai?.status === 'pending' ? ai : null

  useEffect(() => { setItems([]) }, [cid])
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }) }, [items])
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [])

  async function ask(q: string) {
    setAsking(true)
    setItems((xs) => [...xs, { kind: 'qa', q, a: null }])
    const done = (patch: Partial<Item>) => setItems((xs) => xs.map((x, i) => (i === xs.length - 1 && x.kind === 'qa' ? { ...x, ...patch } as Item : x)))
    try {
      const r = await api<{ answer: string }>(`/customers/${cid}/ask`, { method: 'POST', body: JSON.stringify({ message: q, proposal_id: ai?.id ?? null }) })
      done({ a: r.answer })
    } catch (e) {
      done({ a: '', err: (e as Error).message })
    } finally {
      setAsking(false)
    }
  }

  function adjusted(p: Proposal) {
    onAdjusted(p)
    const changes = p.simulation.compare?.changes ?? []
    if (p.simulation.compare) setItems((xs) => [...xs, { kind: 'updated', p, changes }])
  }

  return (
    <>
      {!open && (
        <button className="chat-fab" onClick={() => setOpen(true)} aria-label="Open chat">
          <MessageCircle size={18} strokeWidth={2} />{pending ? 'Ask or change the plan' : 'Ask Paidway'}
        </button>
      )}
      <aside className={`drawer ${open ? 'open' : ''}`} aria-hidden={!open} aria-label="Chat">
        <div className="drawer-head">
          <h2>{pending ? 'Ask or change the plan' : 'Ask about your money'}</h2>
          <button className="icon-btn ghost" onClick={() => setOpen(false)} aria-label="Close chat"><X size={18} strokeWidth={2} /></button>
        </div>
        <div className="drawer-body">
          {items.length === 0 && (
            <p className="small muted" style={{ marginTop: 0 }}>
              {pending ? 'Ask anything about the plan, or say a change ("don\'t touch my savings"). Changes re-plan; nothing moves until you approve.' : 'Ask anything about your accounts. Press Find a plan for a plan you can change here.'}
            </p>
          )}
          {items.map((x, i) => x.kind === 'qa' ? (
            <div key={i} className="qa">
              <div className="q">{x.q}</div>
              {x.a == null ? <div className="a muted">Thinking…</div> : x.err ? <div className="error">{x.err}</div> : <div className="a">{x.a}</div>}
            </div>
          ) : (
            <div key={i} className="notice small updated">
              <span><b>Plan updated:</b> {x.changes.length ? x.changes.join('; ') : 'new plan ready'}</span>
              <button className="ghost" onClick={() => { onSeePlan(x.p); setOpen(false) }}>See plan <ArrowRight size={14} strokeWidth={2} className="ic-inline" /></button>
            </div>
          ))}
          <div ref={end} />
          <Chat ai={pending} bare asking={asking} onQuestion={ask} onAdjusted={adjusted} onRules={onRules} />
        </div>
      </aside>
    </>
  )
}
