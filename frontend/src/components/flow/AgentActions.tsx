import type { ReactNode } from 'react'
import { usd } from '../../lib/api'
import type { ApprovePreview, NessieCall } from '../../lib/api'
import type { Schedule } from '../../lib/flow'
import NessieChip from '../NessieChip'
import { title } from '../../lib/flow'

type Quiet = { step: string; how?: string[] | string; link?: string }

/** "gifts_holidays: $316 -> $190/mo" -> "Gifts holidays: $316 → $190/mo" */
const CAT: Record<string, string> = { gifts_holidays: 'Gifts and holidays', home_household: 'Home and household', car_maintenance: 'Car upkeep', annual_bills: 'Annual bills' }
const pretty = (s: string) => s
  .replace(/^([a-z_]+):/, (_, k: string) => `${CAT[k] ?? title(k)}:`)
  .replace(/->/g, '→')
  .replace(/ across all debts, extra in order$/, ' to debt, highest rate first')
  .replace(/^Autopay full statement on (.+) once at \$0$/, 'Autopay $1 in full once it hits $0')
const tool = (t: string) => t.replace(/^nessie\.write\./, '')
const nth = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th'}`
const day = (s?: string) => (s ? new Date(s + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '')

/** One planned (or sent) Nessie call in plain words. */
function what(c: NessieCall): { verb: string; text: string; when: string } {
  const desc = typeof c.body?.description === 'string' ? (c.body.description as string) : ''
  if (c.tool.endsWith('create_bill'))
    return { verb: 'Recurring bill', text: `${c.from ?? 'Checking'} pays ${c.to ?? 'the debt'}`, when: `/mo · the ${nth(c.recurring_date ?? 1)}${c.first_payment ? `, from ${day(c.first_payment)}` : ''}` }
  if (c.tool.endsWith('create_transfer'))
    return { verb: 'Transfer', text: `${c.from} → ${c.to}`, when: 'today' }
  if (c.tool.endsWith('create_deposit'))
    return { verb: 'Payment', text: `${c.to} paid from ${c.from}`, when: 'today' }
  return { verb: 'Withdrawal', text: c.to?.startsWith('Estimated tax') ? `${c.from}: ${desc || 'estimated tax'}` : `${c.from} pays ${c.to}`, when: 'today' }
}

/** execute.schedule steps (from /plan/extras) as rough calls, when the preview endpoint isn't there. */
function fromSchedule(s: Schedule | null | undefined): NessieCall[] {
  return (s?.steps ?? []).filter((x) => x.runs === 'nessie').map((x) => {
    const n = (x.nessie ?? {}) as { op?: string; from?: string; to?: string; payee?: string; amount?: number; recurring_date?: number; payment_date?: string }
    const op = n.op ?? 'transfer'
    return { tool: `create_${op}`, method: 'POST', path: '', from: n.from, to: n.payee ?? n.to, amount: n.amount ?? Number(x.amount ?? 0), recurring_date: n.recurring_date ?? null, first_payment: n.payment_date, step: x.step }
  })
}

export function callsFor(pv: ApprovePreview | null, schedule: Schedule | null | undefined): { calls: NessieCall[]; recorded: Quiet[]; manual: Quiet[] } {
  if (pv) return { calls: pv.nessie_calls, recorded: pv.recorded, manual: pv.manual }
  const steps = schedule?.steps ?? []
  return {
    calls: fromSchedule(schedule),
    recorded: steps.filter((x) => (x.runs ?? 'recorded') === 'recorded'),
    manual: steps.filter((x) => x.runs === 'manual').map((x) => ({ step: x.step, how: x.how as string[] | undefined, link: x.link as string | undefined })),
  }
}

function status(c: NessieCall) {
  if (c.status === 'error') return <span className="tag red">failed</span>
  if (c.status === 'not_sent') return <span className="tag">not sent</span>
  if (c.status === 'skipped') return <span className="tag">held back</span>
  return <span className="tag good">{typeof c.status === 'number' ? `${c.status} created` : 'done'}</span>
}

/** "What the agent will do on approve" (planned), or the "Done in Nessie" receipt (sent: status and id per row). */
export default function AgentActions({ title, calls, recorded = [], manual = [], receipt = false, note, children }: {
  title: string; calls: NessieCall[]; recorded?: Quiet[]; manual?: Quiet[]; receipt?: boolean; note?: ReactNode; children?: ReactNode
}) {
  return (
    <div className="card agent-do">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{title}</h2>
        {calls.length > 0 && <span className="small muted">{calls.length} Nessie call{calls.length > 1 ? 's' : ''}</span>}
      </div>
      {note && <div className="small muted" style={{ margin: '0 0 6px' }}>{note}</div>}
      {calls.length === 0 && <div className="small muted">Nothing runs in Nessie; the steps below are tracked or yours to do.</div>}
      {calls.map((c, i) => {
        const w = what(c)
        return (
          <div key={i} className="act">
            <NessieChip label={w.verb} title={`Nessie API: ${c.method} ${tool(c.tool)}`} />
            <div style={{ minWidth: 0 }}>
              <div className="what">{w.text}</div>
              {c.note && <div className="small muted">{c.note}</div>}
            </div>
            <div className="amt">{usd(c.amount)}<small>{w.when}</small></div>
            {receipt && <div className="rcpt">{status(c)}{c.id && <code>id {c.id}</code>}{c.error && <span className="bad">{c.error}</span>}{c.repairs?.map((r) => <span key={r} className="small muted">{r}</span>)}{c.retried && <span className="small muted">retried</span>}</div>}
          </div>
        )
      })}
      {(recorded.length > 0 || manual.length > 0) && (
        <div className="quiet">
          {recorded.length > 0 && (<><h3>Tracked each month</h3><ul>{recorded.map((s, i) => <li key={i}>{pretty(s.step)}</li>)}</ul></>)}
          {manual.length > 0 && (<><h3>You do it</h3><ul>{manual.map((s, i) => (
            <li key={i}>{pretty(s.step)}{s.how && <> · {Array.isArray(s.how) ? s.how.join(' · ') : s.how}</>}{s.link && <> · <a href={s.link} target="_blank" rel="noreferrer">link</a></>}</li>
          ))}</ul></>)}
        </div>
      )}
      {children}
    </div>
  )
}
