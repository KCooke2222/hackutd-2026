import { useEffect, useRef, useState } from 'react'
import { fmtDuration, type Proposal, type ReplayRun } from '../../lib/api'
import Steps from './Steps'
import { listenSteps } from '../../lib/steps'
import type { Step } from '../../lib/steps'

type Run = { steps: Step[]; final: string; finalWhy: string; error: string | null; done: boolean }

export type RunMeta = { run_id: string; customer_id: string; status: 'running' | 'done' | 'error'; started_at: number; ended_at: number | null }

/** Streams one real agent run as chips: tool, one number, reasoning collapsed. A new run starts on the server
 *  (/api/agent/stream) and keeps going there if this page goes away; `attach` re-opens an existing run
 *  (/api/agent/runs/{id}/stream: everything so far, then live). With `replay`, plays a recorded run the same way, sped up. */
export default function Thinking({ cid, model, runKey, replay, attach, onRun, onDone }: {
  cid: string; model: string; runKey: number; replay?: ReplayRun | null; attach?: string | null
  onRun?: (m: RunMeta) => void; onDone: (p: Proposal | null, final: string) => void
}) {
  const [run, setRun] = useState<Run>({ steps: [], final: '', finalWhy: '', error: null, done: false })
  const [secs, setSecs] = useState(0)
  const done = useRef(onDone)
  done.current = onDone
  const ran = useRef(onRun)
  ran.current = onRun

  useEffect(() => {
    const fresh = { steps: [], final: '', finalWhy: '', error: null, done: false }
    setRun(fresh); setSecs(0)
    let t0 = Date.now()
    let timer = setInterval(() => setSecs(Math.round((Date.now() - t0) / 1000)), 1000)
    let es: EventSource | null = null
    let runId = attach ?? null
    let tries = 0
    let retry: ReturnType<typeof setTimeout> | undefined
    const open = () => {
      es = new EventSource(replay
        ? `/api/replays/${encodeURIComponent(replay.id)}/stream?speed=${replay.speed}`
        : runId ? `/api/agent/runs/${encodeURIComponent(runId)}/stream`
          : `/api/agent/stream?customer_id=${encodeURIComponent(cid)}`)
      listen(es)
    }
    // Opened on the next tick so StrictMode's mount/unmount/mount doesn't start two real runs.
    const start = setTimeout(open, 0)
    const stop = () => { clearTimeout(start); clearTimeout(retry); es?.close(); clearInterval(timer) }
    const listen = (es: EventSource) => {
      // Every (re-)attach starts with `run` and then the whole run so far, so start the chips over.
      es.addEventListener('run', (e) => {
        const d = JSON.parse((e as MessageEvent).data) as RunMeta
        runId = d.run_id; tries = 0
        t0 = d.started_at * 1000
        setRun(fresh)
        clearInterval(timer)
        if (d.ended_at) setSecs(Math.round(d.ended_at - d.started_at))
        else { setSecs(Math.round((Date.now() - t0) / 1000)); timer = setInterval(() => setSecs(Math.round((Date.now() - t0) / 1000)), 1000) }
        ran.current?.(d)
      })
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
        const raw = (e as MessageEvent).data
        es.close()
        // A dropped connection to a server-side run: the run is still going, so re-attach.
        if (!raw && !replay && runId && tries < 5) { tries++; retry = setTimeout(open, 1000 * tries); return }
        stop()
        const msg = raw ? JSON.parse(raw).error : 'Connection to the agent dropped.'
        setRun((r) => ({ ...r, error: msg, done: true }))
      })
    }
    return stop
  }, [cid, runKey, attach, replay?.id, replay?.speed])

  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{run.done ? 'Plan found' : 'Finding a plan'}</h2>
        <span className="small muted num" style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {replay && (
            <span className="tag warn" title={`Recorded run, ${fmtDuration(replay.duration_ms)} live. No model call.`}>
              Replay · {replay.speed > 0 ? `sped up ${replay.speed}×` : 'instant'}
            </span>
          )}
          {model.split('/').pop()} · {secs}s{replay && ` (live ${fmtDuration(replay.duration_ms)})`}
        </span>
      </div>
      <Steps steps={run.steps} done={run.done} />
      {run.final && <p className="final-line">{run.final}</p>}
      {run.error && <div className="error">{run.error}</div>}
    </div>
  )
}
