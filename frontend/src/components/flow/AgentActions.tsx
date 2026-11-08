import type { ReactNode } from 'react'
import { Check, Clock, X } from 'lucide-react'
import { usd } from '../../lib/api'
import type { AgentStep, ApprovePreview, MonthSplit, NessieCall } from '../../lib/api'
import type { Schedule } from '../../lib/flow'
import NessieChip from '../NessieChip'
import { title } from '../../lib/flow'

type Quiet = { step: string; how?: string[] | string; link?: string }
type Watched = { kind?: string; step: string; watch?: string }

/** "gifts_holidays: $316 -> $190/mo" -> "Gifts holidays: $316 → $190/mo" */
const CAT: Record<string, string> = { gifts_holidays: 'Gifts and holidays', home_household: 'Home and household', car_maintenance: 'Car upkeep', annual_bills: 'Annual bills' }
export const pretty = (s: string) => s
  .replace(/^([a-z_]+):/, (_, k: string) => `${CAT[k] ?? title(k)}:`)
  .replace(/->/g, '→')
  .replace(/ across all debts, extra in order$/, ' to debt, highest rate first')
  .replace(/^Autopay full statement on (.+) once at \$0$/, 'Autopay $1 in full once it hits $0')
const tool = (t: string) => t.replace(/^nessie\.write\./, '')
const nth = (d: number) => `${d}${d % 10 === 1 && d !== 11 ? 'st' : d % 10 === 2 && d !== 12 ? 'nd' : d % 10 === 3 && d !== 13 ? 'rd' : 'th'}`
const day = (s?: string) => (s ? new Date(s + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '')

/** One planned (or sent) Nessie call in plain words. */
export function what(c: NessieCall): { verb: string; text: string; when: string } {
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

export function callsFor(pv: ApprovePreview | null, schedule: Schedule | null | undefined): { calls: NessieCall[]; recorded: Quiet[]; manual: Quiet[]; agent: AgentStep[]; summary: MonthSplit | null } {
  const steps = schedule?.steps ?? []
  const agentOf = () => steps.filter((x) => x.runs === 'agent').map((x) => ({ kind: x.kind, step: x.step, watch: x.watch as string | undefined, amount: x.amount }))
  const summaryOf = () => (steps.find((x) => x.runs === 'summary') as MonthSplit | undefined) ?? null
  if (pv) return { calls: pv.nessie_calls, recorded: pv.recorded, manual: pv.manual, agent: pv.agent ?? agentOf(), summary: pv.summary ?? summaryOf() }
  return {
    calls: fromSchedule(schedule),
    recorded: steps.filter((x) => (x.runs ?? 'recorded') === 'recorded'),
    manual: steps.filter((x) => x.runs === 'manual').map((x) => ({ step: x.step, how: x.how as string[] | undefined, link: x.link as string | undefined })),
    agent: agentOf(),
    summary: summaryOf(),
  }
}

/** "Each month: debt shrinks $196 · $1,423 covers new card purchases · $759 stays in cash". */
export function MonthLine({ s }: { s?: MonthSplit | null }) {
  if (!s || s.paid == null) return null
  const bits = [<><b>{usd(Math.round(s.paid))}/mo</b> goes to your debts</>]
  if ((s.new_purchases ?? 0) >= 1) bits.push(<>{usd(Math.round(s.new_purchases!))} of it covers new card purchases</>)
  bits.push(<>debt shrinks <b>{usd(Math.round(s.net_paydown ?? 0))}/mo</b></>)
  if ((s.left_over ?? 0) >= 1) bits.push(<>{usd(Math.round(s.left_over!))}/mo left over stays in cash</>)
  return <div className="small month-line">Each month: {bits.map((b, i) => <span key={i}>{i ? ' · ' : ''}{b}</span>)}</div>
}

/** Planned view: a payoff (transfer + payment, maybe a modelled sale cost) is one row, not two or three. */
type Planned = { verb: string; text: string; sub?: string; amount: number; when: string; calls: NessieCall[] }
function planned(calls: NessieCall[]): Planned[] {
  const out: Planned[] = []
  const byGroup = new Map<string, Planned>()
  for (const c of calls) {
    const g = c.group && !c.tool.endsWith('create_bill') ? c.group : null
    const row = g ? byGroup.get(g) : undefined
    if (row) {
      row.calls.push(c)
      if (c.tool.endsWith('create_withdrawal')) row.sub = `${row.sub ?? ''} · ${usd(c.amount)} est. sale cost`
      continue
    }
    const w = what(c)
    const r: Planned = c.tool.endsWith('create_transfer') || c.tool.endsWith('create_deposit')
      ? { verb: 'Pay off', text: `${c.to} from ${c.from}`, amount: c.amount, when: 'today', calls: [c] }
      : c.tool.endsWith('create_bill') && c.step
        ? { verb: w.verb, text: pretty(c.step), sub: [w.text, c.note].filter(Boolean).join(' · '), amount: c.amount, when: w.when, calls: [c] }
        : { verb: w.verb, text: w.text, sub: c.note ?? undefined, amount: c.amount, when: w.when, calls: [c] }
    if (g) byGroup.set(g, r)
    out.push(r)
  }
  for (const r of out) if (r.calls.length > 1) r.sub = `Transfer + payment${r.sub ?? ''}`
  return out
}

const failedCall = (c: NessieCall) => c.status === 'error'
const heldCall = (c: NessieCall) => c.status === 'not_sent' || c.status === 'skipped'

/** Receipt mark: a check when Nessie took the call, a cross when it failed, a clock when it wasn't sent. */
function mark(c: NessieCall) {
  if (failedCall(c)) return <span className="rmark bad" title="Didn't go through"><X size={14} strokeWidth={2.75} /></span>
  if (heldCall(c)) return <span className="rmark held" title={c.status === 'skipped' ? 'Held back' : 'Not sent'}><Clock size={13} strokeWidth={2.25} /></span>
  return <span className="rmark ok" title="Done in Nessie"><Check size={14} strokeWidth={2.75} /></span>
}

/** "The agent watches these" (checked at each monthly check-in) and "You do it": the plan steps that aren't Nessie calls. */
export function PlanSteps({ recorded = [], manual = [], agent = [] }: { recorded?: Quiet[]; manual?: Quiet[]; agent?: Watched[] }) {
  if (!recorded.length && !manual.length && !agent.length) return null
  return (
    <div className="quiet">
      {agent.length > 0 && (<><h3>The agent watches these</h3><ul>{agent.map((s, i) => (
        <li key={i}>{pretty(s.step)}{s.watch && <span className="muted"> · {s.watch}</span>}</li>
      ))}</ul></>)}
      {recorded.length > 0 && (<><h3>Recorded with the plan</h3><ul>{recorded.map((s, i) => <li key={i}>{pretty(s.step)}</li>)}</ul></>)}
      {manual.length > 0 && (<><h3>You do it</h3><ul>{manual.map((s, i) => (
        <li key={i}>{pretty(s.step)}{s.how && <> · {Array.isArray(s.how) ? s.how.join(' · ') : s.how}</>}{s.link && <> · <a href={s.link} target="_blank" rel="noreferrer">link</a></>}</li>
      ))}</ul></>)}
    </div>
  )
}

/** "What the agent will do on approve" (planned), or the "Done in Nessie" receipt (sent: a check or a cross per call). */
export default function AgentActions({ title, calls, recorded = [], manual = [], agent = [], summary, receipt = false, note, onRetry, retrying = false, children }: {
  title: string; calls: NessieCall[]; recorded?: Quiet[]; manual?: Quiet[]; agent?: Watched[]; summary?: MonthSplit | null; receipt?: boolean; note?: ReactNode
  onRetry?: () => void; retrying?: boolean; children?: ReactNode
}) {
  return (
    <div className="card agent-do">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{title}</h2>
        {calls.length > 0 && <span className="small muted">{receipt ? '' : `${planned(calls).length} moves · `}{calls.length} Nessie call{calls.length > 1 ? 's' : ''}</span>}
      </div>
      {note && <div className="small muted" style={{ margin: '0 0 6px' }}>{note}</div>}
      {calls.length === 0 && <div className="small muted">Nothing runs in Nessie; the agent watches the steps below, or they're yours to do.</div>}
      {!receipt && planned(calls).map((r, i) => (
        <div key={i} className="act">
          <NessieChip label={r.verb} title={`Nessie API: ${r.calls.map((c) => `${c.method} ${tool(c.tool)}`).join(', ')}`} />
          <div style={{ minWidth: 0 }}>
            <div className="what">{r.text}</div>
            {r.sub && <div className="small muted">{r.sub}</div>}
          </div>
          <div className="amt">{usd(r.amount)}<small>{r.when}</small></div>
        </div>
      ))}
      {receipt && calls.map((c, i) => {
        const w = what(c)
        return (
          <div key={i} className={`act${receipt ? ' receipt' : ''}`}>
            {receipt
              ? <span className="rlabel">{mark(c)}<span>{w.verb}</span></span>
              : <NessieChip label={w.verb} title={`Nessie API: ${c.method} ${tool(c.tool)}`} />}
            <div style={{ minWidth: 0 }}>
              <div className="what">{w.text}</div>
              {c.note && <div className="small muted">{c.note}</div>}
            </div>
            <div className="amt">{usd(c.amount)}<small>{w.when}</small></div>
            {receipt && (failedCall(c) || heldCall(c) || c.retried) && (
              <div className="rcpt">
                {failedCall(c) && <span className="bad" title={c.error ?? undefined}>Didn't go through</span>}
                {heldCall(c) && <span>{c.status === 'skipped' ? 'Held back' : 'Not sent yet'}</span>}
                {failedCall(c) && onRetry && <button className="small" onClick={onRetry} disabled={retrying}>{retrying ? 'Retrying…' : 'Retry'}</button>}
                {c.retried && !failedCall(c) && <span>went through on retry</span>}
              </div>
            )}
          </div>
        )
      })}
      {!receipt && <MonthLine s={summary} />}
      <PlanSteps recorded={recorded} manual={manual} agent={agent} />
      {children}
    </div>
  )
}
