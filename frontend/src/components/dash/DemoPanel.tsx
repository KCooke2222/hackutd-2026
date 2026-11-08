import { useState } from 'react'
import { api } from '../../lib/api'
import type { Checkin, Tracking } from '../../lib/api'
import { ym } from '../../lib/flow'

/** Dev/demo control: fast-forward simulated months under a scenario. Writes demo rows only, never real accounts. */
export default function DemoPanel({ cid, t, onData }: { cid: string; t: Tracking; onData: (t: Tracking) => void }) {
  const [scenario, setScenario] = useState('on_plan')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [ci, setCi] = useState<Checkin | null>(null)
  const done = t.month_index >= t.horizon

  async function call(path: string, body?: object) {
    setBusy(true); setErr(null)
    try {
      const r = await api<Tracking & { checkin?: Checkin }>(`/customers/${cid}/tracking/${path}`, { method: 'POST', body: JSON.stringify(body ?? {}) })
      setCi(r.checkin ?? null)
      onData(r)
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="demo">
      <div className="demo-row">
        <span className="tag warn">Simulation</span>
        <span className="small muted">Demo months, not real transactions · {t.month_index === 0 ? 'month 0' : `month ${t.month_index}, ${ym(t.now)}`}</span>
      </div>
      <div className="demo-row">
        <select aria-label="scenario" value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {t.scenarios.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <button className="primary" disabled={busy || done} onClick={() => call('step', { scenario })}>Fast-forward a month</button>
        <button disabled={busy || done} onClick={() => call('checkin', { scenario })}>{busy ? 'Checking in…' : 'Fast-forward + agent check-in'}</button>
        <button disabled={busy || t.month_index === 0} onClick={() => call('reset')}>Reset</button>
      </div>
      {t.history.length > 0 && (
        <div className="demo-hist">
          {t.history.map((h) => <span key={h.month_index} className="tag">{h.month_index}: {t.scenarios.find((s) => s.key === h.scenario)?.label ?? h.scenario}</span>)}
        </div>
      )}
      {ci && (
        <div className="notice small">
          <b>Check-in: {ci.drift}</b>
          {!!ci.facts.length && <div className="muted">{ci.facts.join(' · ')}</div>}
          {ci.why && <div>{ci.why}</div>}
          {ci.proposal_id && <div>Proposed: <b>{ci.headline}</b> (pending your approval)</div>}
        </div>
      )}
      {err && <div className="error small">{err}</div>}
    </div>
  )
}
