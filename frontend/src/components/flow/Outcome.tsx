import { useEffect, useState } from 'react'
import { Check } from 'lucide-react'
import { api, usd } from '../../lib/api'
import type { ApprovePreview, BalanceSheet, Proposal } from '../../lib/api'
import type { Preview } from '../../lib/flow'
import NessieChip from '../NessieChip'
import AgentActions from './AgentActions'
import NetWorth from './NetWorth'
import PlanTimeline from './PlanTimeline'

/** After approve: the Nessie receipt (each call with status and id), the balances after, then the net-worth path. */
export default function Outcome({ preview, approved, planned, sheet: before }: { preview: Preview | null; approved: Proposal; planned?: ApprovePreview | null; sheet?: BalanceSheet | null }) {
  const ex = approved.execution ?? {}
  const sim = preview?.sim ?? approved.simulation
  const [sheet, setSheet] = useState<BalanceSheet | null>(null)
  useEffect(() => { api<BalanceSheet>(`/customers/${approved.customer_id}/balance-sheet`).then(setSheet).catch(() => setSheet(null)) }, [approved.id])

  const sent = ex.nessie_calls
  const held = ex.nessie_pending
  const recorded = (ex.scheduled ?? []).filter((s) => (s.runs ?? 'recorded') === 'recorded')
  const manual = (ex.scheduled ?? []).filter((s) => s.runs === 'manual')
  const prior = new Map((before?.accounts ?? []).map((a) => [a.account_id, a.balance]))
  const live = (sheet?.accounts ?? []).filter((a) => a.nessie_id || a.source === 'nessie')
  return (
    <>
      {sent || held ? (
        <AgentActions title={sent?.length ? 'Done in Nessie' : held?.length ? 'Approved, not sent to Nessie' : 'Approved'} receipt={!!sent?.length}
          calls={sent?.length ? sent : held ?? []} recorded={recorded} manual={manual}
          note={held?.length ? `${ex.note ?? 'Nessie unavailable'}: these calls were recorded, not sent.` : ex.error ? <span className="bad">{ex.error}</span> : undefined} />
      ) : (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Done</h2><span className={`tag status-${approved.status}`}>{approved.status}</span></div>
          {ex.mode === 'nessie' && ex.amount && <div className="move"><span className="ok"><Check size={14} strokeWidth={2.5} className="ic-inline" /></span><span>Savings → PNC card</span><b className="num">{usd(ex.amount)}</b><span className="sub">Nessie transfer executed</span></div>}
          {(ex.scheduled ?? []).map((s, i) => (
            <div key={i} className="move"><span className="ok">◷</span><span>{s.step}</span><b className="num" /><span className="sub">Scheduled{s.how?.length ? ` · ${s.how.join(' · ')}` : ''}</span></div>
          ))}
          {!ex.scheduled?.length && planned && <div className="small muted">{planned.nessie_calls.length} Nessie call(s) planned.</div>}
          {ex.error && <div className="error">{ex.error}</div>}
        </div>
      )}
      {live.length > 0 && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Balances now</h2><NessieChip label="Read from Nessie" /></div>
          {live.map((a) => {
            const was = prior.get(a.account_id)
            const moved = was != null && Math.abs(was - a.balance) >= 0.01
            return (
              <div key={a.account_id} className="lrow">
                <span className="name">{a.name}</span>
                <span className="amt">{moved && <span className="small muted" style={{ textDecoration: 'line-through', marginRight: 6 }}>{usd(was!)}</span>}{usd(a.balance)}</span>
              </div>
            )
          })}
        </div>
      )}
      <div className="card">
        <h2 style={{ margin: '0 0 6px' }}>Net worth</h2>
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>
      <PlanTimeline cid={approved.customer_id} plan={approved.plan} />
    </>
  )
}
