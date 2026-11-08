import { useState } from 'react'
import { api, usd } from '../../lib/api'
import type { ApprovePreview, BalanceSheet, MonthSplit, Proposal } from '../../lib/api'
import { contribLines } from '../../lib/flow'
import type { Preview } from '../../lib/flow'
import NessieChip from '../NessieChip'
import DoneList from './DoneList'
import NetWorth from './NetWorth'
import { BillDiffList } from './RecurringBills'

const nth = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th'}`

/** After approve: "Done" with a checklist of what ran in Nessie and what's left, balances in Nessie (before → after), the recurring bills, net worth. */
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
  const agent = (ex.scheduled ?? []).filter((s) => s.runs === 'agent')
  const summary = ((ex.scheduled ?? []).find((s) => s.runs === 'summary') as MonthSplit | undefined) ?? planned?.summary ?? null
  const bal = ex.nessie_check ?? []
  const bills = ex.bills ?? []
  // "IRA paused until Apr 2027; restarts automatically" comes from the bill diff, else from the plan's contributions.
  const diff = (ex.recurring ?? planned?.recurring)?.filter((r) => r.action !== 'keep')

  async function retry() {
    setBusy(true); setErr(null)
    try { onChange?.(await api<Proposal>(`/proposals/${approved.id}/retry`, { method: 'POST' })) }
    catch (e) { setErr((e as Error).message) }
    finally { setBusy(false) }
  }

  const receipt = (
    <DoneList calls={sent?.length ? sent : held ?? []} held={!sent?.length && !!held?.length}
      recorded={recorded} manual={manual} agent={agent} summary={summary} onRetry={ex.retry_available ? retry : undefined} retrying={busy}
      note={held?.length && !sent?.length ? `${ex.note ?? 'Nessie unavailable'}.` : !sent && !held && planned ? `${planned.nessie_calls.length} Nessie call(s) planned.` : undefined}>
      <BillDiffList diff={diff} schedule={ex.bill_schedule ?? planned?.bill_schedule} />
      {!diff?.some((r) => r.action === 'pause') && contribLines(sim).map((t) => <div key={t} className="small">{t}</div>)}
      {err && <div className="error">{err}</div>}
      {ex.error && <div className="error">{ex.error}</div>}
    </DoneList>
  )

  // "Done" + the checklist first (Nessie moves ticked, steps left open), then balances in Nessie, the recurring bills, net worth.
  return (
    <>
      {receipt}
      {bal.length > 0 && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Balances in Nessie now</h2><NessieChip label="Read from Nessie" title="GET /accounts/{id} plus Paidway's Nessie transactions on it" /></div>
          {bal.map((a) => {
            const moved = Math.abs(a.after - a.before) >= 0.5
            return (
              <div key={a.account_id} className="lrow">
                <span className="name">{a.name}</span>
                <span className="amt">{moved && <span className="small muted" style={{ textDecoration: 'line-through', marginRight: 6 }}>{usd(a.before)}</span>}{usd(a.after)}</span>
                {moved && <span className="sub"><span className={a.ok ? 'ok' : 'bad'}>{a.after < a.before ? '−' : '+'}{usd(Math.abs(a.after - a.before))}</span></span>}
              </div>
            )
          })}
        </div>
      )}
      {bills.length > 0 && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Recurring bills</h2><NessieChip label="Nessie bills" /></div>
          {bills.map((b) => (
            <div key={b.id ?? b.payee} className="lrow">
              <span className="name">{b.payee}</span>
              <span className="amt">{usd(b.amount)}/mo</span>
              <span className="sub"><span>the {nth(b.day)} of each month{b.from ? ` from ${b.from.replace(/^C\d+\s+/, '')}` : ''}</span></span>
            </div>
          ))}
        </div>
      )}
      <div className="card">
        <h2 style={{ margin: '0 0 6px' }}>Net worth</h2>
        <NetWorth sim={sim} baseline={preview?.baseline ?? null} />
      </div>
    </>
  )
}
