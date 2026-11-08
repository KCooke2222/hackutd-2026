import { usd } from '../../lib/api'
import type { Proposal } from '../../lib/api'
import type { Preview } from '../../lib/flow'
import NetWorth from './NetWorth'

/** After approve: the net-worth path and what was executed vs scheduled. */
export default function Outcome({ preview, approved }: { preview: Preview | null; approved: Proposal }) {
  const ex = approved.execution ?? {}
  const sim = preview?.sim ?? approved.simulation
  const moved = ex.mode === 'nessie' && ex.amount
  return (
    <>
      <div className="card">
        <h2 style={{ margin: '0 0 6px' }}>Net worth</h2>
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>
      <div className="card">
        <div className="col-head"><h2 style={{ margin: 0 }}>Done</h2><span className={`tag status-${approved.status}`}>{approved.status}</span></div>
        {moved && <div className="move"><span className="ok">✓</span><span>Savings → PNC card</span><b className="num">{usd(ex.amount)}</b><span className="sub">Nessie transfer executed</span></div>}
        {(ex.transfers_pending ?? []).map((t) => (
          <div key={t.to_debt} className="move"><span className="ok">✓</span><span>Savings → PNC card</span><b className="num">{usd(t.amount)}</b><span className="sub">Recorded locally{ex.note ? ` · ${ex.note}` : ''}</span></div>
        ))}
        {(ex.scheduled ?? []).map((s, i) => (
          <div key={i} className="move"><span className="ok">◷</span><span>{s.step}</span><b className="num" /><span className="sub">Scheduled{s.how?.length ? ` · ${s.how.join(' · ')}` : ''}</span></div>
        ))}
        {ex.error && <div className="error">{ex.error}</div>}
      </div>
    </>
  )
}
