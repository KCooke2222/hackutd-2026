import { useEffect, useRef, useState } from 'react'
import { api, usd } from '../../lib/api'
import type { Compare, Proposal, RuleLabel } from '../../lib/api'
import { ym } from '../../lib/flow'
import Steps from './Steps'
import { listenSteps } from '../../lib/steps'
import type { Step } from '../../lib/steps'

const SUGGEST = ["Don't touch my savings", 'Keep 6 months of cash', 'I can cut dining more', "Don't cut shopping"]

type Run = { steps: Step[]; done: boolean; error: string | null; clarify: string | null; added: RuleLabel[]; dropped: string[] }
type Result = { compare: Compare; proposal: Proposal; request: string }

// Browser speech-to-text (Chrome/Edge/Safari). Not in TS's DOM lib, so typed loosely.
type Recognition = { lang: string; interimResults: boolean; onresult: ((e: any) => void) | null; onend: (() => void) | null; onerror: (() => void) | null; start: () => void; stop: () => void }
const SpeechCtor: (new () => Recognition) | undefined =
  typeof window !== 'undefined' ? ((window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition) : undefined

const signed = (n: number) => `${n >= 0 ? '+' : '−'}${usd(Math.abs(n))}`

function CompareCard({ r, busy, onUndo, onKeep }: { r: Result; busy: boolean; onUndo: () => void; onKeep: () => void }) {
  const c = r.compare
  const warn = r.proposal.simulation.explain?.warning
  const f = c.high_cost_debt_free
  return (
    <div className="card" style={{ margin: '10px 0 0', borderColor: c.worse ? '#fecaca' : undefined }}>
      <div className="small muted">“{r.request}”</div>
      <div className="ba" style={{ marginTop: 6 }}>
        <div><div className="k">Net worth, 2 yrs</div><div className="from">{usd(c.net_worth_24m.before)}</div>
          <div className={`to ${c.net_worth_24m.delta < 0 ? 'bad' : 'good'}`}>{usd(c.net_worth_24m.after)} <span className="small">{signed(c.net_worth_24m.delta)}</span></div></div>
        <div><div className="k">Interest, 2 yrs</div><div className="from">{usd(c.interest_total.before)}</div>
          <div className={`to ${c.interest_total.delta > 0 ? 'bad' : ''}`}>{usd(c.interest_total.after)}</div></div>
        <div><div className="k">High-cost $0</div><div className="from">{ym(f.before)}</div>
          <div className={`to ${f.before !== f.after && (f.months_later == null || f.months_later > 0) ? 'bad' : 'good'}`}>{ym(f.after)}</div></div>
      </div>
      {warn && <div className="error" style={{ margin: '10px 0 0' }}>{warn}. Applied anyway: it's your call.</div>}
      <ul className="small" style={{ margin: '8px 0 0', paddingLeft: 18 }}>
        {c.changes.length ? c.changes.map((x) => <li key={x}>{x}</li>) : <li className="muted">No levers changed</li>}
      </ul>
      <div className="actions" style={{ marginTop: 10 }}>
        <button className="primary" onClick={onKeep} disabled={busy}>Keep this</button>
        <button onClick={onUndo} disabled={busy}>Undo: back to previous plan</button>
      </div>
    </div>
  )
}

/** Talk to the plan: type or speak a change; the agent re-plans from the current plan under your rules. */
export default function Chat({ ai, onAdjusted, onRules }: {
  ai: Proposal
  onAdjusted: (p: Proposal) => void // the new pending plan (adjusted, or the parent after Undo)
  onRules: (p: Proposal) => void // same plan, rules changed
}) {
  const [text, setText] = useState('')
  const [run, setRun] = useState<Run | null>(null)
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [listening, setListening] = useState(false)
  const es = useRef<EventSource | null>(null)
  const rec = useRef<Recognition | null>(null)
  const rules = ai.simulation.constraint_labels ?? []
  const running = run != null && !run.done

  useEffect(() => () => { es.current?.close(); rec.current?.stop() }, [])

  function send(msg: string) {
    const m = msg.trim()
    if (!m || running) return
    setErr(null); setResult(null)
    setRun({ steps: [], done: false, error: null, clarify: null, added: [], dropped: [] })
    const s = new EventSource(`/api/proposals/${ai.id}/adjust/stream?message=${encodeURIComponent(m)}`)
    es.current = s
    const end = (patch: Partial<Run>) => { s.close(); setRun((r) => (r ? { ...r, done: true, ...patch } : r)) }
    listenSteps(s, (f) => setRun((r) => (r ? { ...r, steps: f(r.steps) } : r)))
    s.addEventListener('constraints', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      setRun((r) => (r ? { ...r, added: d.added ?? [], dropped: d.dropped ?? [] } : r))
    })
    s.addEventListener('done', (e) => {
      const d = JSON.parse((e as MessageEvent).data)
      if (d.clarify) return end({ clarify: d.clarify })
      end({})
      if (d.proposal && d.compare) {
        setResult({ compare: d.compare, proposal: d.proposal, request: m })
        setText('')
        onAdjusted(d.proposal)
      }
    })
    s.addEventListener('error', (e) => {
      const raw = (e as MessageEvent).data
      end({ error: raw ? JSON.parse(raw).error : 'Connection to the agent dropped (is the plan still pending?).' })
    })
  }

  function mic() {
    if (!SpeechCtor) return
    if (listening) { rec.current?.stop(); return }
    const r = new SpeechCtor()
    r.lang = 'en-US'
    r.interimResults = true
    r.onresult = (e) => setText(Array.from(e.results as ArrayLike<any>).map((x) => x[0].transcript).join(''))
    r.onend = () => setListening(false)
    r.onerror = () => setListening(false)
    rec.current = r
    setListening(true)
    r.start()
  }

  async function undo() {
    if (!result) return
    setBusy(true); setErr(null)
    try {
      const parent = await api<Proposal>(`/proposals/${result.proposal.id}/restore`, { method: 'POST' })
      setResult(null); setRun(null)
      onAdjusted(parent)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function dropRule(r: RuleLabel) {
    setBusy(true); setErr(null)
    try {
      onRules(await api<Proposal>(`/proposals/${ai.id}/constraints/remove`, { method: 'POST', body: JSON.stringify({ key: r.key, item: r.item }) }))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h3 style={{ margin: '0 0 6px' }}>Change the plan</h3>
      {rules.length > 0 && (
        <div className="chips" title="Your rules: every plan must follow them">
          {rules.map((r) => (
            <span key={`${r.key}:${r.item}`} className="tag" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
              {r.label}
              <button className="ghost" aria-label={`remove rule ${r.label}`} disabled={busy || running} onClick={() => dropRule(r)}>×</button>
            </span>
          ))}
        </div>
      )}
      <form style={{ display: 'flex', gap: 6 }} onSubmit={(e) => { e.preventDefault(); send(text) }}>
        <input style={{ flex: 1 }} value={text} onChange={(e) => setText(e.target.value)} disabled={running}
          placeholder={listening ? 'Listening…' : 'e.g. "don\'t touch my savings"'} aria-label="change the plan" maxLength={500} />
        {SpeechCtor && <button type="button" onClick={mic} disabled={running} aria-label="speak" className={listening ? 'primary' : ''}>{listening ? '■' : '🎤'}</button>}
        <button className="primary" type="submit" disabled={running || !text.trim()}>{running ? 'Working…' : 'Send'}</button>
      </form>
      {!run && !result && (
        <div className="chips" style={{ marginTop: 8 }}>
          {SUGGEST.map((s) => <button key={s} className="chip" onClick={() => send(s)}>{s}</button>)}
        </div>
      )}
      {run && (running || run.error || run.clarify) && <Steps steps={run.steps} done={run.done} waiting="Reading your plan" />}
      {run && run.added.length > 0 && <div className="small muted" style={{ marginTop: 6 }}>New rule: {run.added.map((r) => r.label).join(' · ')}</div>}
      {run && run.dropped.length > 0 && <div className="small muted">Couldn't use: {run.dropped.join('; ')}</div>}
      {run?.clarify && <div className="notice small">{run.clarify}</div>}
      {run?.error && <div className="error">{run.error}</div>}
      {err && <div className="error">{err}</div>}
      {result && <CompareCard r={result} busy={busy} onUndo={undo} onKeep={() => { setResult(null); setRun(null) }} />}
    </div>
  )
}
