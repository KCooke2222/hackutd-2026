import { useEffect, useRef, useState } from 'react'
import type { Proposal } from '../../lib/api'
import Steps from './Steps'
import { listenSteps } from '../../lib/steps'
import type { Step } from '../../lib/steps'

type Run = { steps: Step[]; final: string; finalWhy: string; error: string | null; done: boolean }

/** Streams one real agent run (SSE from /api/agent/stream) as chips: tool, one number, reasoning collapsed. */
export default function Thinking({ cid, model, runKey, onDone }: { cid: string; model: string; runKey: number; onDone: (p: Proposal | null, final: string) => void }) {
  const [run, setRun] = useState<Run>({ steps: [], final: '', finalWhy: '', error: null, done: false })
  const [secs, setSecs] = useState(0)
  const done = useRef(onDone)
  done.current = onDone

  useEffect(() => {
    setRun({ steps: [], final: '', finalWhy: '', error: null, done: false }); setSecs(0)
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
      listenSteps(es, (f) => setRun((r) => ({ ...r, steps: f(r.steps) })))
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
      <Steps steps={run.steps} done={run.done} />
      {run.final && <p className="final-line">{run.final}</p>}
      {run.error && <div className="error">{run.error}</div>}
    </div>
  )
}
