import { useEffect, useRef, useState } from 'react'
import type { Proposal } from '../../lib/api'

type Step = { id: string; name: string; label: string; why: string; result?: string }
type Run = { steps: Step[]; final: string; finalWhy: string; error: string | null; done: boolean }

const ICON: Record<string, string> = {
  phase_gather: '≡', phase_diagnose: '?', phase_explain: '✎',
  sql: '⌕', cash_flow: '⇄', spending_detail: '▦', find_recurring: '↻', compare_sources: '⚖', debt_priority: '#',
  liquidity_ladder: '▤', simulate_plan: '∿', propose_plan: '✓',
}

/** Streams one real agent run (SSE from /api/agent/stream) as chips: tool, one number, reasoning collapsed. */
export default function Thinking({ cid, model, runKey, onDone }: { cid: string; model: string; runKey: number; onDone: (p: Proposal | null, final: string) => void }) {
  const [run, setRun] = useState<Run>({ steps: [], final: '', finalWhy: '', error: null, done: false })
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [secs, setSecs] = useState(0)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    setRun({ steps: [], final: '', finalWhy: '', error: null, done: false }); setOpen({}); setSecs(0)
    const t0 = Date.now()
    const timer = setInterval(() => setSecs(Math.round((Date.now() - t0) / 1000)), 1000)
    // Opened on the next tick so StrictMode's mount/unmount/mount doesn't start two real runs.
    let es: EventSource | null = null
    const start = setTimeout(() => {
      es = new EventSource(`/api/agent/stream?customer_id=${encodeURIComponent(cid)}`)
      listen(es)
    }, 0)
    const stop = () => { clearTimeout(start); es?.close(); clearInterval(timer) }
    const listen = (es: EventSource) => {
      es.addEventListener('call', (e) => {
        const d = JSON.parse((e as MessageEvent).data)
        setRun((r) => ({ ...r, steps: [...r.steps, { id: d.id, name: d.name, label: d.label, why: d.why }] }))
      })
      es.addEventListener('result', (e) => {
        const d = JSON.parse((e as MessageEvent).data)
        setRun((r) => ({ ...r, steps: r.steps.map((s) => (s.id === d.id ? { ...s, result: d.result } : s)) }))
      })
      es.addEventListener('final', (e) => {
        const d = JSON.parse((e as MessageEvent).data)
        setRun((r) => ({ ...r, final: d.text, finalWhy: d.why }))
      })
      es.addEventListener('done', (e) => {
        stop()
        const d = JSON.parse((e as MessageEvent).data)
        setRun((r) => ({ ...r, done: true }))
        done.current(d.proposal ?? null, d.final ?? '')
      })
      es.addEventListener('error', (e) => {
        stop()
        const raw = (e as MessageEvent).data
        const msg = raw ? JSON.parse(raw).error : 'Connection to the agent dropped.'
        setRun((r) => ({ ...r, error: msg, done: true }))
      })
    }
    return stop
  }, [cid, runKey])

  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{run.done ? 'Plan found' : 'Finding a plan'}</h2>
        <span className="small muted num">{model.split('/').pop()} · {secs}s</span>
      </div>
      <div style={{ marginTop: 6 }}>
        {run.steps.map((s) => {
          const running = s.result == null && !run.done
          return (
            <div key={s.id} className={`step ${running ? 'run' : ''}`}>
              <span className="ic">{running ? <span className="spin" /> : ICON[s.name] ?? '•'}</span>
              <span>{s.label}{s.why && <button className="tog" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>{open[s.id] ? 'hide' : 'why'}</button>}</span>
              <span className="res">{s.result ?? '…'}</span>
              {open[s.id] && <span className="why">{s.why}</span>}
            </div>
          )
        })}
        {!run.steps.length && !run.error && <div className="step run"><span className="ic"><span className="spin" /></span><span className="muted">Reading your accounts</span><span /></div>}
      </div>
      {run.final && <p className="final-line">{run.final}</p>}
      {run.error && <div className="error">{run.error}</div>}
    </div>
  )
}
