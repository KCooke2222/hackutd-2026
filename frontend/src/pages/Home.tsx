import { useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'
import { api, fmtDuration } from '../lib/api'
import type { AppliedOption, ApprovePreview, BalanceSheet, Proposal, RealityCheck as RC, ReplayMeta, ReplayRun, SimV2, Tracking } from '../lib/api'
import { isV2, knobsFrom, planFrom } from '../lib/flow'
import type { Knobs, Preview, Schedule } from '../lib/flow'
import Snapshot from '../components/flow/Snapshot'
import Thinking from '../components/flow/Thinking'
import type { RunMeta } from '../components/flow/Thinking'
import Outcome from '../components/flow/Outcome'
import PlanReport from '../components/flow/PlanReport'
import AgentActions, { callsFor } from '../components/flow/AgentActions'
import ChatDrawer from '../components/flow/ChatDrawer'
import RealityCheck from '../components/flow/RealityCheck'
import UnderwaterOptions from '../components/flow/UnderwaterOptions'
import Scenarios from '../components/flow/Scenarios'
import IncomeExpense from '../components/IncomeExpense'
import Brand from '../components/Brand'
import Dashboard from '../components/dash/Dashboard'
import DemoPanel from '../components/dash/DemoPanel'

type Health = { nessie: boolean; nvidia: boolean; model: string }
// The two demo stories; the other generated customers stay in the data but not in the UI.
const EXAMPLES: [string, string][] = [['C001', 'Fixable'], ['C027', 'Underwater']]
const NAMES = ['Snapshot', 'Thinking', 'Plan', 'Done']

// Last agent run per customer ({cid: run id}), so a reload or a customer switch can re-attach to it.
const RUNS_KEY = 'paidway.runs'
function storedRuns(): Record<string, string> {
  try { return JSON.parse(localStorage.getItem(RUNS_KEY) ?? '{}') ?? {} } catch { return {} }
}
function storeRun(c: string, id: string | null) {
  try {
    const r = storedRuns()
    if (id) r[c] = id
    else delete r[c]
    localStorage.setItem(RUNS_KEY, JSON.stringify(r))
  } catch { /* private window: re-attach falls back to /agent/runs/latest */ }
}

export default function Home() {
  const [cid, setCid] = useState(() => new URLSearchParams(location.search).get('customer') ?? 'C001')
  const [health, setHealth] = useState<Health | null>(null)
  const [sheet, setSheet] = useState<BalanceSheet | null>(null)
  const [screen, setScreen] = useState(0)
  const [reached, setReached] = useState(0)
  const [runKey, setRunKey] = useState(0)
  // A server-side run to re-open in Thinking (null: Thinking starts a new run, or plays `replay`).
  const [attach, setAttach] = useState<string | null>(null)
  const epoch = useRef(0)
  const [thinkingDone, setThinkingDone] = useState(false)
  const [ai, setAi] = useState<Proposal | null>(null)
  const [past, setPast] = useState<Proposal[]>([])
  const lastPending = past.find((p) => p.status === 'pending') ?? null
  const urlPlan = useRef(new URLSearchParams(location.search).get('plan'))
  const urlReplay = useRef(new URLSearchParams(location.search).get('replay'))
  const [knobs, setKnobs] = useState<Knobs | null>(null)
  const [outlook, setOutlook] = useState<Preview | null>(null)
  const [approved, setApproved] = useState<Proposal | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  // Home is the goal dashboard once the customer has an approved plan (view 'dash'); the 4-step flow otherwise.
  const [track, setTrack] = useState<Tracking | null>(null)
  const [view, setView] = useState<'dash' | 'flow'>('flow')
  const [demo, setDemo] = useState(() => new URLSearchParams(location.search).get('demo') === '1')

  async function loadTrack(show: boolean) {
    const t = await api<Tracking | { active: false }>(`/customers/${cid}/tracking`).catch(() => null)
    const on = t && t.active ? t : null
    setTrack(on)
    trackRef.current = on
    if (show) setView(on ? 'dash' : 'flow')
    return on
  }
  const [reality, setReality] = useState<RC | null>(null)
  // Options the customer chose to close an underwater gap; the agent plans with them.
  const [applied, setApplied] = useState<AppliedOption[]>([])
  function loadReality(c: string) {
    api<RC>(`/customers/${c}/reality-check`).then(setReality).catch(() => setReality(null))
    api<{ options: AppliedOption[] }>(`/customers/${c}/options/applied`).then((r) => setApplied(r.options)).catch(() => setApplied([]))
  }
  const [planTab, setPlanTab] = useState<'plan' | 'whatif'>('plan')
  // For the plan page: approve's exact Nessie calls, and the same plan without its suggested cuts (net worth chart).
  const [pv, setPv] = useState<ApprovePreview | null>(null)
  const [extras, setExtras] = useState<{ no_cuts: SimV2 | null; schedule: Schedule | null } | null>(null)
  const [replays, setReplays] = useState<ReplayMeta[]>([])
  const [replay, setReplay] = useState<ReplayRun | null>(null)
  const pendingReplay = useRef<ReplayRun & { customer_id: string } | null>(null)
  const cidRef = useRef(cid)
  cidRef.current = cid
  const trackRef = useRef<Tracking | null>(null)

  useEffect(() => {
    api<Health>('/health').then(setHealth)
    // ?replay=<id>&speed=8 plays a recorded run on load (switching customer first if needed).
    const q = new URLSearchParams(window.location.search)
    const rid = q.get('replay')
    if (rid) {
      const speed = Number(q.get('speed') ?? 8)
      api<ReplayMeta>(`/replays/${encodeURIComponent(rid)}`).then((m) => {
        const run = { id: m.id, speed: Number.isFinite(speed) && speed >= 0 ? speed : 8, duration_ms: m.duration_ms }
        pendingReplay.current = { ...run, customer_id: m.customer_id }
        if (cidRef.current === m.customer_id) startPending(m.customer_id)
        else setCid(m.customer_id)
      }).catch(() => { urlReplay.current = null; setNote(`Cached run ${rid} not found.`) })
    }
  }, [])

  function startPending(c: string) {
    const p = pendingReplay.current
    if (!p || p.customer_id !== c) return
    pendingReplay.current = null
    urlReplay.current = null
    findPlan({ id: p.id, speed: p.speed, duration_ms: p.duration_ms })
  }

  useEffect(() => {
    setSheet(null); setReality(null); setAi(null); setKnobs(null); setOutlook(null); setApproved(null); setErr(null)
    setScreen(0); setReached(0); setRunKey(0); setAttach(null); setThinkingDone(false); setTrack(null); trackRef.current = null
    const n = ++epoch.current
    const tracked = loadTrack(!urlPlan.current && !urlReplay.current)
    // Re-attach to this customer's last run if the server still has it (it keeps going without us).
    if (!urlPlan.current && !urlReplay.current) {
      const c = cid
      const id = storedRuns()[c]
      const known = id ? api<RunMeta>(`/agent/runs/${encodeURIComponent(id)}`).catch(() => { storeRun(c, null); return null }) : Promise.resolve(null)
      const run = known.then((m) => m ?? api<RunMeta | null>(`/agent/runs/latest?customer_id=${encodeURIComponent(c)}`).catch(() => null))
      Promise.all([tracked, run]).then(([, m]) => {
        if (!m || n !== epoch.current) return
        storeRun(c, m.run_id)
        setReplay(null); setAttach(m.run_id); setThinkingDone(false); setRunKey((k) => k + 1); setReached((r) => Math.max(r, 1))
        if (m.status === 'running') { setView('flow'); setScreen(1) }
      })
    }
    api<BalanceSheet>(`/customers/${cid}/balance-sheet`).then(setSheet).catch((e) => setErr(e.message))
    setApplied([]); loadReality(cid)
    api<Proposal[]>(`/proposals?customer_id=${cid}`).then((ps) => {
      const v2 = ps.filter((p) => isV2(p)).sort((a, b) => b.created_at.localeCompare(a.created_at))
      setPast(v2)
      // ?plan=<id> or ?plan=last opens a saved run straight away (for UI checks without a model run)
      const want = urlPlan.current
      if (want) {
        urlPlan.current = null
        const hit = want === 'last' ? v2.find((p) => p.status === 'pending') ?? v2[0] : v2.find((p) => p.id === want)
        if (hit) openPlan(hit)
      }
    })
    setReplays([])
    api<ReplayMeta[]>(`/replays?customer_id=${cid}`).then(setReplays).catch(() => {})
    startPending(cid)
  }, [cid])

  // Live preview for the sliders, debounced; stale responses are dropped.
  const seq = useRef(0)
  useEffect(() => {
    if (!knobs || !ai) return
    const n = ++seq.current
    const t = setTimeout(() => {
      api<Preview>(`/customers/${cid}/plan/simulate`, { method: 'POST', body: JSON.stringify({ plan: planFrom(ai, knobs), constraints: ai.constraints ?? null }) })
        .then((o) => { if (n === seq.current) setOutlook(o) })
        .catch((e) => n === seq.current && setErr(e.message))
    }, 200)
    return () => clearTimeout(t)
  }, [knobs, cid, ai])

  useEffect(() => {
    setPv(null); setExtras(null)
    if (!ai) return
    let live = true
    api<ApprovePreview>(`/proposals/${ai.id}/preview`).then((r) => live && setPv(r)).catch(() => {})
    api<{ no_cuts: SimV2 | null; schedule: Schedule | null }>(`/customers/${ai.customer_id}/plan/extras`, { method: 'POST', body: JSON.stringify({ plan: ai.plan }) })
      .then((r) => live && setExtras(r)).catch(() => {})
    return () => { live = false }
  }, [ai?.id])

  function go(i: number) {
    setView('flow'); setScreen(i); setReached((r) => Math.max(r, i)); setErr(null)
  }

  function openPlan(p: Proposal) {
    setAi(p); setKnobs(knobsFrom(p)); setApproved(null); go(2)
  }

  /** Talk to the plan produced a new pending plan (or Undo restored the previous one). */
  function adjusted(p: Proposal) {
    const prev = ai?.id
    setPast((ps) => [p, ...ps.filter((x) => x.id !== p.id).map((x) => (x.id === prev ? { ...x, status: 'superseded' } : x))])
    setAi(p); setKnobs(knobsFrom(p)); setApproved(null); setErr(null)
  }

  /** Start a live agent run, or (with `rp`) replay a recorded one through the same Thinking screen. */
  function findPlan(rp: ReplayRun | null = null) {
    epoch.current++
    setAttach(null); setNote(null); setThinkingDone(false); setAi(null); setApproved(null); setReplay(rp); setReached(1); setRunKey((k) => k + 1); go(1)
  }

  async function approve() {
    if (!ai || !knobs) return
    setBusy(true); setErr(null)
    try {
      const p = await api<Proposal>(`/proposals/${ai.id}/approve`, { method: 'POST', body: JSON.stringify({ plan: planFrom(ai, knobs) }) })
      setApproved(p); setPast((ps) => ps.map((x) => (x.id === ai.id ? { ...x, status: 'rejected' } : x))); go(3); loadTrack(false)
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

  async function openById(id: string) {
    const ps = await api<Proposal[]>(`/proposals?customer_id=${cid}`)
    const v2 = ps.filter((p) => isV2(p)).sort((a, b) => b.created_at.localeCompare(a.created_at))
    setPast(v2)
    const hit = v2.find((p) => p.id === id)
    if (hit) { setReached(2); openPlan(hit) }
  }

  /** Logo click: the dashboard if there's an approved plan, else the Snapshot step. A run in progress keeps going. */
  function home() {
    setNote(null)
    if (trackRef.current) { setErr(null); setView('dash') } else go(0)
  }

  const dash = view === 'dash' && track != null
  const canGo = (j: number) => j <= reached && (j !== 3 || approved != null) && (j !== 2 || ai != null)

  // The snapshot reads live Nessie balances: refetch each time it is shown, so a demo reset shows up.
  useEffect(() => {
    if (screen === 0) api<BalanceSheet>(`/customers/${cid}/balance-sheet`).then(setSheet).catch(() => {})
  }, [screen, cid])

  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-in">
          <Brand onHome={home} />
          <nav className="dots" aria-label="Steps">
            {NAMES.map((n, j) => (
              <button key={n} className={!dash && j === screen ? 'on' : j < reached ? 'done' : ''} disabled={!canGo(j)} onClick={() => go(j)} aria-current={!dash && j === screen ? 'step' : undefined}>
                <span className="n">{j < reached && (dash || j !== screen) ? <Check size={12} strokeWidth={3} /> : j + 1}</span>{n}
              </button>
            ))}
          </nav>
          <div className="seg who" role="tablist" aria-label="Example customer">
            {EXAMPLES.map(([c, label]) => (
              <button key={c} role="tab" className={cid === c ? 'on' : ''} aria-selected={cid === c} onClick={() => setCid(c)}>{label}</button>
            ))}
            {!EXAMPLES.some(([c]) => c === cid) && <button role="tab" className="on" aria-selected>{cid}</button>}
          </div>
        </div>
      </header>
      <main className="page flow">
      {health && !health.nvidia && <div className="notice small">No NVIDIA_API_KEY: the agent can't run.</div>}
      {note && <div className="notice small">{note}</div>}
      {err && screen !== 2 && <div className="error">{err}</div>}

      {dash && (
        <>
          <div className="dash-bar"><button className="ghost" onClick={() => setDemo((d) => !d)}>{demo ? 'Hide demo' : 'Demo'}</button></div>
          {demo && <DemoPanel cid={cid} t={track} onData={(t) => setTrack(t)} />}
          <Dashboard cid={cid} t={track} onChose={openById} onReplan={() => findPlan()} onSnapshot={() => go(0)} />
        </>
      )}
      <div style={{ display: dash ? 'none' : 'block' }}>

      {screen === 0 && sheet && <Snapshot sheet={sheet} />}
      {screen === 0 && <IncomeExpense cid={cid} />}
      {runKey > 0 && (
        // Stays mounted after the run so Back/Next doesn't start another one.
        <div style={{ display: screen === 1 ? 'block' : 'none' }}>
          <Thinking key={cid} cid={cid} model={replay ? (replays.find((r) => r.id === replay.id)?.model ?? health?.model ?? '') : health?.model ?? ''} runKey={runKey} replay={replay} attach={attach}
            onRun={(m) => storeRun(m.customer_id, m.run_id)}
            onDone={(p) => { setThinkingDone(true); if (isV2(p)) { setAi(p); setKnobs(knobsFrom(p)); setPast((ps) => [p, ...ps.filter((x) => x.id !== p.id)]); setReached((r) => Math.max(r, 2)) } }} />
        </div>
      )}
      {screen === 1 && thinkingDone && !ai && reality && !reality.realistic && <RealityCheck rc={reality} />}
      {screen === 2 && ai && knobs && (
        <div className="seg plan-tabs" role="tablist">
          <button role="tab" className={planTab === 'plan' ? 'on' : ''} onClick={() => setPlanTab('plan')}>Plan</button>
          <button role="tab" className={planTab === 'whatif' ? 'on' : ''} onClick={() => setPlanTab('whatif')}>What if</button>
        </div>
      )}
      {screen === 2 && !ai && reality && !reality.realistic && <RealityCheck rc={reality} />}
      {screen === 2 && ai && knobs && planTab === 'plan' && (
        <PlanReport ai={ai} baseline={outlook?.baseline ?? null} noCuts={extras?.no_cuts ?? null} preview={pv} schedule={extras?.schedule ?? null}
          underwater={reality && !reality.realistic ? (
            <UnderwaterOptions key={cid} cid={cid} gap={reality.gap_per_month} cards={ai.simulation.explain?.underwater_options} noHelp={ai.simulation.explain?.underwater_not_helping}
              onApplied={() => { loadReality(cid); findPlan() }} />
          ) : undefined}
          builtOn={applied.length ? (
            <div className="card">
              <h2 style={{ margin: '0 0 4px' }}>Built on what you'll do</h2>
              <ol className="pr-steps">
                {applied.map((o) => (
                  <li key={o.key}><span><b>{o.title}</b></span><span className="runs"><span className="tag warn">You do it</span></span>
                    <span className="sub">{o.how}{o.link && <> · <a href={o.link} target="_blank" rel="noreferrer">where</a></>}</span></li>
                ))}
              </ol>
            </div>
          ) : undefined}>
          <AgentActions title={ai.status === 'pending' ? 'What the agent will do on approve' : 'What approve does'} {...callsFor(pv, extras?.schedule)}
            note={pv && (!pv.nessie_enabled || !pv.seeded) ? "This customer isn't set up in Nessie yet: approve records these calls without sending them." : undefined}>
            {outlook && !outlook.valid && <div className="error">{outlook.reasons.join('; ')}</div>}
            {err && <div className="error">{err}</div>}
            {ai.status === 'pending' && (
              <div className="actions" style={{ marginTop: 14 }}>
                <button className="primary big" onClick={approve} disabled={busy || (outlook != null && !outlook.valid)}>{busy ? 'Approving…' : 'Approve'}</button>
                <button className="big" onClick={reject} disabled={busy}>Reject</button>
              </div>
            )}
          </AgentActions>
        </PlanReport>
      )}
      {screen === 2 && ai && knobs && planTab === 'whatif' && <Scenarios cid={cid} plan={planFrom(ai, knobs)} />}
      {screen === 3 && approved && <Outcome preview={outlook} approved={approved} planned={pv} sheet={sheet} />}

      <div className="flow-nav">
        <button disabled={screen === 0} style={{ visibility: screen === 0 ? 'hidden' : 'visible' }} onClick={() => go(screen - 1)}>Back</button>
        {screen === 0 && (
          <span className="flow-actions">
            {past.length > 0 && (
              <select aria-label="Previous runs" value="" onChange={(e) => { const p = past.find((x) => x.id === e.target.value); if (p) openPlan(p) }}>
                <option value="">Previous runs ({past.length})</option>
                {past.map((p) => (
                  <option key={p.id} value={p.id}>{new Date(p.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {p.status}{p.simulation?.explain?.headline ? ` · ${p.simulation.explain.headline}` : ''}</option>
                ))}
              </select>
            )}
            {lastPending && <button className="ghost" onClick={() => openPlan(lastPending)}>Open last plan</button>}
            {replays.length > 0 && (
              <select value="" aria-label="Load cached run" title="Replay a recorded agent run (no model call), sped up 8×"
                onChange={(e) => { const r = replays.find((x) => x.id === e.target.value); if (r) findPlan({ id: r.id, speed: 8, duration_ms: r.duration_ms }) }}>
                <option value="" disabled>Load cached run</option>
                {replays.map((r) => (
                  <option key={r.id} value={r.id}>{new Date(r.recorded_at).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {fmtDuration(r.duration_ms)} · {r.headline || r.id}</option>
                ))}
              </select>
            )}
            <button className="primary" onClick={() => findPlan()} disabled={!health?.nvidia}>Find a plan</button>
          </span>
        )}
        {screen === 1 && <button className="primary" disabled={!thinkingDone || !ai} onClick={() => go(2)}>See plan</button>}
        {screen === 3 && (
          <span className="flow-actions">
            <button onClick={() => { setNote(null); go(0) }}>Start over</button>
            <button className="primary" onClick={() => loadTrack(true)}>Go to dashboard</button>
          </span>
        )}
      </div>
      </div>
      </main>
      <ChatDrawer cid={cid} ai={ai} onAdjusted={adjusted} onRules={setAi} onSeePlan={(p) => { setPlanTab('plan'); openPlan(p) }} />
    </div>
  )
}
