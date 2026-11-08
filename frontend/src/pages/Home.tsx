import { useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { BalanceSheet, Proposal } from '../lib/api'
import { isV2, knobsFrom, planFrom } from '../lib/flow'
import type { Knobs, Preview } from '../lib/flow'
import Snapshot from '../components/flow/Snapshot'
import type { Summary } from '../components/flow/Snapshot'
import Thinking from '../components/flow/Thinking'
import PlanEditor from '../components/flow/PlanEditor'
import Outcome from '../components/flow/Outcome'
import PlanTimeline from '../components/flow/PlanTimeline'
import IncomeExpense from '../components/IncomeExpense'

type Health = { nessie: boolean; nvidia: boolean; model: string }
const NAMES = ['Snapshot', 'Thinking', 'Plan', 'Done']

const firstSentence = (s: string) => {
  const w = (s.match(/^.*?[.!?](\s|$)/)?.[0] ?? s).trim().split(/\s+/)
  return w.length > 12 ? `${w.slice(0, 12).join(' ')}…` : w.join(' ')
}

export default function Home() {
  const [cid, setCid] = useState('C001')
  const [ids, setIds] = useState<string[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [sheet, setSheet] = useState<BalanceSheet | null>(null)
  const [summary, setSummary] = useState<Summary | null>(null)
  const [screen, setScreen] = useState(0)
  const [reached, setReached] = useState(0)
  const [runKey, setRunKey] = useState(0)
  const [thinkingDone, setThinkingDone] = useState(false)
  const [ai, setAi] = useState<Proposal | null>(null)
  const [past, setPast] = useState<Proposal[]>([])
  const lastPending = past.find((p) => p.status === 'pending') ?? null
  const urlPlan = useRef(new URLSearchParams(location.search).get('plan'))
  const [final, setFinal] = useState('')
  const [knobs, setKnobs] = useState<Knobs | null>(null)
  const [outlook, setOutlook] = useState<Preview | null>(null)
  const [approved, setApproved] = useState<Proposal | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    api<{ customer_id: string }[]>('/customers').then((l) => setIds(l.map((c) => c.customer_id)))
    api<Health>('/health').then(setHealth)
  }, [])

  useEffect(() => {
    setSheet(null); setSummary(null); setAi(null); setKnobs(null); setOutlook(null); setApproved(null); setErr(null)
    setScreen(0); setReached(0); setRunKey(0)
    api<BalanceSheet>(`/customers/${cid}/balance-sheet`).then(setSheet).catch((e) => setErr(e.message))
    api<Summary>(`/customers/${cid}/summary`).then(setSummary).catch((e) => setErr(e.message))
    api<Proposal[]>(`/proposals?customer_id=${cid}`).then((ps) => {
      const v2 = ps.filter((p) => isV2(p)).sort((a, b) => b.created_at.localeCompare(a.created_at))
      setPast(v2)
      // ?plan=<id> or ?plan=last opens a saved run straight away (for UI checks without a model run)
      const want = urlPlan.current
      if (want) {
        urlPlan.current = null
        const hit = want === 'last' ? v2.find((p) => p.status === 'pending') ?? v2[0] : v2.find((p) => p.id === want)
        if (hit) openPlan(hit, '')
      }
    })
  }, [cid])

  // Live preview for the sliders, debounced; stale responses are dropped.
  const seq = useRef(0)
  useEffect(() => {
    if (!knobs || !ai) return
    const n = ++seq.current
    const t = setTimeout(() => {
      api<Preview>(`/customers/${cid}/plan/simulate`, { method: 'POST', body: JSON.stringify({ plan: planFrom(ai, knobs) }) })
        .then((o) => { if (n === seq.current) setOutlook(o) })
        .catch((e) => n === seq.current && setErr(e.message))
    }, 200)
    return () => clearTimeout(t)
  }, [knobs, cid, ai])

  function go(i: number) {
    setScreen(i); setReached((r) => Math.max(r, i)); setErr(null)
  }

  function openPlan(p: Proposal, why: string) {
    setAi(p); setFinal(why); setKnobs(knobsFrom(p)); setApproved(null); go(2)
  }

  function findPlan() {
    setNote(null); setThinkingDone(false); setAi(null); setApproved(null); setReached(1); setRunKey((k) => k + 1); go(1)
  }

  async function approve() {
    if (!ai || !knobs) return
    setBusy(true); setErr(null)
    try {
      const p = await api<Proposal>(`/proposals/${ai.id}/approve`, { method: 'POST', body: JSON.stringify({ plan: planFrom(ai, knobs) }) })
      setApproved(p); setPast((ps) => ps.map((x) => (x.id === ai.id ? { ...x, status: 'rejected' } : x))); go(3)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function reject() {
    if (!ai) return
    setBusy(true)
    try {
      await api(`/proposals/${ai.id}/reject`, { method: 'POST' })
      setAi(null); setKnobs(null); setPast((ps) => ps.map((x) => (x.id === ai.id ? { ...x, status: 'rejected' } : x))); setReached(0); setScreen(0); setNote('Plan rejected. Nothing moved.')
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const canGo = (j: number) => j <= reached && (j !== 3 || approved != null) && (j !== 2 || ai != null)

  return (
    <div className="page flow">
      <div className="row">
        <div className="dots">
          {NAMES.map((n, j) => (
            <button key={n} className={j === screen ? 'on' : j < reached ? 'done' : ''} disabled={!canGo(j)} onClick={() => go(j)}>{j + 1} {n}</button>
          ))}
        </div>
        <select value={cid} onChange={(e) => setCid(e.target.value)} aria-label="customer">
          {(ids.length ? ids : [cid]).map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      {health && !health.nvidia && <div className="notice small">No NVIDIA_API_KEY: the agent can't run.</div>}
      {note && <div className="notice small">{note}</div>}
      {err && screen !== 2 && <div className="error">{err}</div>}

      {screen === 0 && sheet && summary && <Snapshot sheet={sheet} summary={summary} />}
      {screen === 0 && <IncomeExpense cid={cid} />}
      {runKey > 0 && (
        // Stays mounted after the run so Back/Next doesn't start another one.
        <div style={{ display: screen === 1 ? 'block' : 'none' }}>
          <Thinking key={cid} cid={cid} model={health?.model ?? ''} runKey={runKey}
            onDone={(p, f) => { setThinkingDone(true); if (isV2(p)) { setAi(p); setFinal(f); setKnobs(knobsFrom(p)); setPast((ps) => [p, ...ps.filter((x) => x.id !== p.id)]) } }} />
        </div>
      )}
      {screen === 2 && ai && knobs && (
        <PlanEditor ai={ai} headline={ai.simulation.explain?.headline ?? (final ? firstSentence(final) : firstSentence(ai.rationale))} knobs={knobs} setKnobs={setKnobs}
          preview={outlook} busy={busy} error={err} onApprove={approve} onReject={reject} />
      )}
      {screen === 2 && ai && knobs && <PlanTimeline cid={cid} plan={planFrom(ai, knobs)} />}
      {screen === 3 && approved && <Outcome preview={outlook} approved={approved} />}

      <div className="flow-nav">
        <button disabled={screen === 0} style={{ visibility: screen === 0 ? 'hidden' : 'visible' }} onClick={() => go(screen - 1)}>Back</button>
        {screen === 0 && (
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {past.length > 0 && (
              <select aria-label="Previous runs" value="" onChange={(e) => { const p = past.find((x) => x.id === e.target.value); if (p) openPlan(p, '') }}>
                <option value="">Previous runs ({past.length})</option>
                {past.map((p) => (
                  <option key={p.id} value={p.id}>{new Date(p.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {p.status}{p.simulation?.explain?.headline ? ` · ${p.simulation.explain.headline}` : ''}</option>
                ))}
              </select>
            )}
            {lastPending && <button className="ghost" onClick={() => openPlan(lastPending, '')}>Open last plan</button>}
            <button className="primary" onClick={findPlan} disabled={!health?.nvidia}>Find a plan</button>
          </span>
        )}
        {screen === 1 && <button className="primary" disabled={!thinkingDone || !ai} onClick={() => go(2)}>See plan</button>}
        {screen === 3 && <button onClick={() => { setNote(null); go(0) }}>Start over</button>}
      </div>
    </div>
  )
}
