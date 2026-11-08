import { useEffect, useState } from 'react'
import { api, usd } from '../../lib/api'
import type { BillDiff, BillScheduleEntry, NessieBills } from '../../lib/api'
import NessieChip from '../NessieChip'
import { mon } from './AgentActions'

const GROUP: Record<string, string> = { debt: 'Debt payments', contribution: 'Contributions', bill: 'Fixed bills', subscription: 'Subscriptions' }

/** "Recurring in Nessie": every recurring outflow as a Nessie recurring bill. Variable spending stays purchases. */
export default function RecurringBills({ cid }: { cid: string }) {
  const [d, setD] = useState<NessieBills | null>(null)
  useEffect(() => { setD(null); api<NessieBills>(`/customers/${cid}/nessie-bills`).then(setD).catch(() => setD(null)) }, [cid])
  if (!d || !d.bills.length) return null
  const kinds = [...new Set(d.bills.map((b) => b.kind))]
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="col-head">
        <span className="col-title"><h2 style={{ margin: 0 }}>Recurring in Nessie</h2>{d.seeded && <NessieChip title="Recurring bills on the checking account in Nessie (Capital One sandbox)" />}</span>
        <span className="total">{usd(d.total)}/mo</span>
      </div>
      {!d.seeded && <p className="small muted" style={{ margin: '4px 0 8px' }}>Not seeded in Nessie yet: these are the bills seeding creates.</p>}
      {kinds.map((k) => (
        <div key={k}>
          <div className="group-label">{GROUP[k] ?? k}</div>
          {d.bills.filter((b) => b.kind === k).map((b) => (
            <div key={b.key} className={`lrow ${b.status === 'paused' ? 'dim' : ''}`}>
              <span className="name">{b.name}</span><span className="amt">{usd(b.amount)}</span>
              <span className="sub">
                {b.day != null && <span className="small muted">day {b.day}</span>}
                {b.status === 'paused' && <span className="tag warn">paused{b.resume ? ` until ${mon(b.resume)}` : ''}</span>}
              </span>
            </div>
          ))}
        </div>
      ))}
      <p className="small muted" style={{ margin: '8px 0 0' }}>Groceries, dining and other variable spending stay card purchases.</p>
    </div>
  )
}

const VERB: Record<BillDiff['action'], string> = { keep: 'Keep', change: 'Change', pause: 'Pause', end: 'End', new: 'New' }

/** The plan as a diff against "Recurring in Nessie": what approve changes (PUT / DELETE / POST), what it keeps
 *  (no call), and what the monthly check-in changes later (a debt hits $0, a pause ends). */
export function BillDiffList({ diff = [], schedule = [] }: { diff?: BillDiff[]; schedule?: BillScheduleEntry[] }) {
  if (!diff.length && !schedule.length) return null
  const moved = diff.filter((r) => r.action !== 'keep')
  const kept = diff.filter((r) => r.action === 'keep')
  return (
    <div className="quiet bill-diff">
      {diff.length > 0 && <h3>Recurring bills in Nessie</h3>}
      {moved.length > 0 && <ul>{moved.map((r) => (
        <li key={r.key}>
          <span className={`tag ${r.action === 'pause' || r.action === 'end' ? 'warn' : ''}`}>{VERB[r.action]}</span>{' '}
          <b>{r.name}</b>{' '}
          {r.action === 'change' && <>{usd(r.from)} → {usd(r.to)}/mo</>}
          {r.action === 'new' && <>{usd(r.to)}/mo</>}
          {r.action === 'end' && <>{usd(r.from)}/mo stops (paid off)</>}
          {r.action === 'pause' && <>{usd(r.from)}/mo paused{r.resume ? ` until ${mon(r.resume)}; restarts automatically` : ''}</>}
        </li>
      ))}</ul>}
      {kept.length > 0 && <div className="small muted">Unchanged: {kept.map((r) => r.name).join(', ')}</div>}
      {schedule.length > 0 && (<><h3>Later, at the monthly check-in</h3><ul>{schedule.map((e, i) => (
        <li key={i} className={e.done ? 'muted' : ''}>
          <b>{mon(e.month)}</b> · {e.action === 'resume' ? `Restart ${e.name}` : e.action === 'end' ? `End ${e.name}` : e.action === 'new' ? `New bill: ${e.name}` : `${e.name}`}
          {e.amount != null && e.action !== 'end' && <> {e.was != null && e.action === 'change' ? <>{usd(e.was)} → </> : null}{usd(e.amount)}/mo</>}
          {e.why && <span className="muted"> · {e.why}</span>}
          {e.done && <span className="muted"> · done</span>}
        </li>
      ))}</ul></>)}
    </div>
  )
}
