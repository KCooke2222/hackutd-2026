import { useState } from 'react'
import { api, usd } from '../../lib/api'
import type { ApprovePreview, BalanceSheet, Proposal } from '../../lib/api'
import type { Preview } from '../../lib/flow'
import NessieChip from '../NessieChip'
import AgentActions from './AgentActions'
import NetWorth from './NetWorth'

const nth = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th'}`

/** After approve: balances in Nessie (before → after), the recurring bills created, the call receipt, net worth. */
export default function Outcome({ preview, approved, planned, onChange }: {
  preview: Preview | null; approved: Proposal; planned?: ApprovePreview | null; sheet?: BalanceSheet | null; onChange?: (p: Proposal) => void
}) {
  const ex = approved.execution ?? {}
  const sim = preview?.sim ?? approved.simulation
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const sent = ex.nessie_calls
  const held = ex.nessie_pending
  const recorded = (ex.scheduled ?? []).filter((s) => (s.runs ?? 'recorded') === 'recorded')
  const manual = (ex.scheduled ?? []).filter((s) => s.runs === 'manual')
  const bal = ex.nessie_check ?? []
  const bills = ex.bills ?? []
  const failed = (sent ?? []).filter((c) => c.status === 'error' || c.status === 'skipped' || c.status === 'not_sent').length

  async function retry() {
    setBusy(true); setErr(null)
    try { onChange?.(await api<Proposal>(`/proposals/${approved.id}/retry`, { method: 'POST' })) }
    catch (e) { setErr((e as Error).message) }
    finally { setBusy(false) }
  }

  return (
    <>
      {bal.length > 0 && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Balances in Nessie now</h2><NessieChip label="Read from Nessie" title="GET /accounts/{id} plus Paidway's Nessie transactions on it" /></div>
          {bal.map((a) => {
            const moved = Math.abs(a.after - a.before) >= 0.5
            return (
              <div key={a.account_id} className="lrow">
                <span className="name">{a.name}</span>
                <span className="amt">{moved && <span className="small muted" style={{ textDecoration: 'line-through', marginRight: 6 }}>{usd(a.before)}</span>}{usd(a.after)}</span>
                <span className="sub"><code>{a.nessie_id}</code>{moved && <span className={a.ok ? 'ok' : 'bad'}>{a.after < a.before ? '−' : '+'}{usd(Math.abs(a.after - a.before))}</span>}</span>
              </div>
            )
          })}
        </div>
      )}
      {bills.length > 0 && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Recurring changes</h2><NessieChip label="Nessie bills" /></div>
          {bills.map((b) => (
            <div key={b.id ?? b.payee} className="lrow">
              <span className="name">{b.payee}</span>
              <span className="amt">{usd(b.amount)}/mo</span>
              <span className="sub"><span>the {nth(b.day)}{b.from ? ` from ${b.from}` : ''}</span>{b.id && <code>{b.id}</code>}</span>
            </div>
          ))}
        </div>
      )}
      {sent || held ? (
        <AgentActions title={failed ? `${failed} Nessie call${failed > 1 ? 's' : ''} didn't go through` : sent?.length ? 'Done in Nessie' : held?.length ? 'Approved, not sent to Nessie' : 'Approved'} receipt={!!sent?.length}
          calls={sent?.length ? sent : held ?? []} recorded={recorded} manual={manual}
          note={held?.length ? `${ex.note ?? 'Nessie unavailable'}: these calls were recorded, not sent.` : failed ? 'Your plan is approved and tracked; the rest went through.' : undefined}>
          {ex.retry_available && failed > 0 && (
            <div className="actions" style={{ marginTop: 10 }}>
              <button className="primary" onClick={retry} disabled={busy}>{busy ? 'Retrying…' : `Retry ${failed}`}</button>
            </div>
          )}
          {err && <div className="error">{err}</div>}
        </AgentActions>
      ) : (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Done</h2><span className={`tag status-${approved.status}`}>{approved.status}</span></div>
          {(ex.scheduled ?? []).map((s, i) => (
            <div key={i} className="move"><span className="ok">◷</span><span>{s.step}</span><b className="num" /><span className="sub">Scheduled{s.how?.length ? ` · ${s.how.join(' · ')}` : ''}</span></div>
          ))}
          {!ex.scheduled?.length && planned && <div className="small muted">{planned.nessie_calls.length} Nessie call(s) planned.</div>}
          {ex.error && <div className="error">{ex.error}</div>}
        </div>
      )}
      <div className="card">
        <h2 style={{ margin: '0 0 6px' }}>Net worth</h2>
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>
    </>
  )
}
