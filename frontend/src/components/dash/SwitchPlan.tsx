import { useState } from 'react'
import { api, usd } from '../../lib/api'
import type { SwitchOption } from '../../lib/api'
import { ym } from '../../lib/flow'

/** Off track: two server-computed ways back. Choosing one saves a PENDING plan; nothing is approved or moved. */
export default function SwitchPlan({ cid, sw, onChose, onReplan }: {
  cid: string; sw: { options: SwitchOption[]; replan: boolean }; onChose: (proposalId: string) => void; onReplan: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [saved, setSaved] = useState<{ id: string; headline: string } | null>(null)
  const [err, setErr] = useState<string | null>(null)

  async function choose(key: string) {
    setBusy(key); setErr(null)
    try {
      const r = await api<{ proposal_id: string; headline: string }>(`/customers/${cid}/tracking/choose`, { method: 'POST', body: JSON.stringify({ option: key }) })
      setSaved({ id: r.proposal_id, headline: r.headline })
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="card switch pop-late">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Switch plan?</h2>
        <span className="small muted">Pick one. It's saved for you to review; nothing changes until you approve.</span>
      </div>
      {saved ? (
        <div className="notice">
          <b>Saved: {saved.headline}.</b> It's pending.{' '}
          <button className="primary" onClick={() => onChose(saved.id)}>Review it</button>
        </div>
      ) : (
        <div className="switch-opts">
          {sw.options.map((o) => <Option key={o.key} o={o} busy={busy} onChoose={() => choose(o.key)} />)}
        </div>
      )}
      {err && <div className="error">{err}</div>}
      {sw.replan && !saved && (
        <div className="notice small">Neither option gets you there. <button className="primary" onClick={onReplan}>Run full re-plan</button></div>
      )}
    </div>
  )
}

function Option({ o, busy, onChoose }: { o: SwitchOption; busy: string | null; onChoose: () => void }) {
  const lock = o.key === 'lock_in'
  return (
    <div className={`opt ${o.feasible ? '' : 'off'}`}>
      <span className="k">{lock ? 'a · Keep the date' : 'b · Move the date'}</span>
      <h3>{!lock && o.feasible && o.months_later === 0 ? `Stay on ${ym(o.new_month)}` : o.title}</h3>
      {lock ? (
        <>
          <div className="big num">{o.feasible ? `+${usd(o.extra_per_month)}/mo` : 'Not enough'}</div>
          <div className="small muted">{o.feasible ? 'to debt, from:' : o.why}</div>
          {(o.sources ?? []).filter((s) => s.amount >= 1).map((s) => (
            <div key={s.label} className="move">
              <span className="ok" style={{ color: 'var(--accent)' }}>→</span>
              <span>{s.label}{s.detail && <span className="small muted"> · {s.detail}</span>}</span>
              <b className="num">{usd(s.amount)}</b>
            </div>
          ))}
          {(o.steps ?? []).map((s) => <div key={s} className="move"><span className="ok">◷</span><span>{s}</span><span /></div>)}
        </>
      ) : (
        <>
          <div className="big num">{o.feasible ? ym(o.new_month) : 'Never'}</div>
          <div className="small muted">
            Same effort, {usd(o.monthly_debt_budget)}/mo to debt
            {o.feasible && (o.months_later ? ` · ${o.months_later} mo later` : ' · same month')}
          </div>
        </>
      )}
      <button className={o.feasible ? 'primary' : ''} disabled={!o.feasible || busy != null} onClick={onChoose}>
        {busy === o.key ? 'Saving…' : lock ? 'Keep the date' : 'Move the date'}
      </button>
    </div>
  )
}
