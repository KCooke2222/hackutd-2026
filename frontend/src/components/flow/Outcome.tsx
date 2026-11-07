import { Area, CartesianGrid, ComposedChart, Line, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usd } from '../../lib/api'
import type { Proposal } from '../../lib/api'
import { monthLabel, payoffLabel } from '../../lib/flow'
import type { Knobs, Outlook } from '../../lib/flow'

const GREY = '#9ca3af', ACCENT = '#ea580c', GOOD = '#16a34a', BAD = '#dc2626'

type Pt = { m: number; today: number; paydown: number; plan: number; band: [number, number] }

/** Card balance for the next N months: three engine paths (finance.project), not drawn by hand. */
function PayoffChart({ o, narrow }: { o: Outlook; narrow: boolean }) {
  const { today, paydown, plan } = o.scenarios
  const N = o.summary.months
  const data: Pt[] = []
  const pt = (m: number, p: number): Pt => ({ m, today: today.balances[m], paydown: paydown.balances[m], plan: p, band: [Math.min(p, today.balances[m]), today.balances[m]] })
  // Month 0 before the lump sum, then the drop it makes: a vertical step at "now".
  if (plan.start_balance - plan.balances[0] > 0.5) data.push({ ...pt(0, plan.start_balance), band: [plan.start_balance, plan.start_balance] })
  for (let m = 0; m <= N; m++) data.push(pt(m, plan.balances[m]))

  const payoff = o.summary.debt_free_month
  const relapse = o.summary.relapse_month
  const saved = o.summary.interest_saved_total
  const lm = Math.round(N * 0.62)
  const labelY = (today.balances[lm] + plan.balances[lm]) / 2
  const ticks = narrow ? [0, Math.round(N / 2), N] : [0, 6, 12, 18, 24].filter((t) => t <= N)
  const short = (v: number) => (v >= 1000 ? `$${(v / 1000).toFixed(v % 1000 ? 1 : 0)}k` : `$${v}`)

  return (
    <ResponsiveContainer width="100%" height={narrow ? 230 : 280}>
      <ComposedChart data={data} margin={{ top: 22, right: 16, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="#e5e7eb" vertical={false} />
        <XAxis dataKey="m" type="number" domain={[0, N]} ticks={ticks} tickFormatter={monthLabel} tick={{ fontSize: 11, fill: '#6b7280' }} tickLine={false} axisLine={{ stroke: '#e5e7eb' }} />
        <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: '#6b7280' }} width={44} tickLine={false} axisLine={false} domain={[0, 'auto']} />
        <Tooltip
          formatter={(v, name) => [Array.isArray(v) ? '' : usd(Number(v)), name]}
          labelFormatter={(m) => monthLabel(Number(m))}
          filterNull
          itemSorter={(i) => -Number(Array.isArray(i.value) ? 0 : i.value)}
        />
        <Area dataKey="band" stroke="none" fill={ACCENT} fillOpacity={0.08} isAnimationActive={false} legendType="none" tooltipType="none" />
        <Line dataKey="today" name="Today" stroke={GREY} strokeWidth={2.5} dot={false} type="linear" isAnimationActive={false} />
        <Line dataKey="paydown" name="Pay down only" stroke={GREY} strokeDasharray="5 4" strokeWidth={2} dot={false} type="linear" isAnimationActive={false} />
        <Line dataKey="plan" name="Plan" stroke={ACCENT} strokeWidth={3} dot={false} type="linear" isAnimationActive={false} />
        {saved > 1 && (
          <ReferenceDot x={lm} y={labelY} r={0} ifOverflow="extendDomain"
            label={{ value: `${usd(saved)} interest saved`, fill: ACCENT, fontSize: narrow ? 12 : 14, fontWeight: 700, position: 'center' }} />
        )}
        {payoff != null && (
          <ReferenceDot x={payoff} y={0} r={5} fill={GOOD} stroke="#fff" ifOverflow="visible"
            label={{ value: `$0 ${payoffLabel(payoff).toLowerCase() === 'now' ? 'now' : monthLabel(payoff)}`, fill: GOOD, fontSize: 12, fontWeight: 700, position: 'insideBottomLeft', offset: 10 }} />
        )}
        {relapse != null && <ReferenceDot x={relapse} y={plan.balances[relapse]} r={4} fill={BAD} stroke="#fff" label={{ value: 'refills', fill: BAD, fontSize: 11, position: 'top' }} />}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

type Move = { what: string; amount: string; sub: string }

function moves(p: Proposal, k: Knobs, o: Outlook): Move[] {
  const ex = (p.execution ?? {}) as { mode?: string; note?: string; transfer?: unknown; manual_steps?: { tier: string; amount: number; one_time_cost: number }[] }
  const out: Move[] = []
  for (const m of ex.manual_steps ?? []) {
    out.push({ what: m.tier === 'cd' ? 'Break CD → card' : 'Sell brokerage → card', amount: usd(m.amount), sub: `You do this step · cost ${usd(m.one_time_cost)}` })
  }
  const sav = (p.plan.lump_sum_from_savings ?? 0)
  if (sav > 0) out.push({ what: 'Savings → card', amount: usd(sav), sub: ex.mode === 'nessie' ? 'Nessie transfer executed' : `Recorded locally${ex.note ? ` · ${ex.note}` : ''}` })
  out.push({ what: 'Card spending cap', amount: `${usd(k.cap)}/mo`, sub: 'Weekly check against the cap' })
  if (k.autopay) out.push({ what: 'Autopay full statement', amount: 'on', sub: o.summary.freed_monthly > 0 ? `${usd(o.summary.freed_monthly)}/mo freed → savings` : 'Card stays at $0' })
  else if (k.pay && Math.abs(k.pay - o.current.monthly_payment) > 1) out.push({ what: 'Card payment', amount: `${usd(k.pay)}/mo`, sub: `was ${usd(o.current.monthly_payment)}` })
  return out
}

export default function Outcome({ o, knobs, approved }: { o: Outlook; knobs: Knobs; approved: Proposal | null }) {
  const narrow = typeof window !== 'undefined' && window.innerWidth < 560
  const sm = o.summary
  return (
    <>
      <div className="card">
        <div className="col-head">
          <h2 style={{ margin: 0 }}>Card balance</h2>
          <div className="legend">
            <span><i style={{ borderColor: GREY }} />Today</span>
            <span><i className="dash" style={{ borderColor: GREY }} />Pay down only{sm.paydown_only_month != null ? ` · $0 ${payoffLabel(sm.paydown_only_month)}` : ''}</span>
            <span><i style={{ borderColor: ACCENT }} />Plan</span>
          </div>
        </div>
        <div style={{ marginTop: 8 }}><PayoffChart o={o} narrow={narrow} /></div>
        <div className="outs">
          <div><div className="k">Debt-free</div><div className={`v ${sm.debt_free_month != null ? 'good' : 'bad'}`}>{payoffLabel(sm.debt_free_month)}</div></div>
          <div><div className="k">Interest saved / yr</div><div className="v">{usd(sm.interest_saved_12m)}</div></div>
          <div><div className="k">Freed / mo</div><div className="v">{usd(sm.freed_monthly)}</div></div>
        </div>
      </div>
      {approved && (
        <div className="card">
          <div className="col-head"><h2 style={{ margin: 0 }}>Done</h2><span className={`tag status-${approved.status}`}>{approved.status}</span></div>
          {moves(approved, knobs, o).map((m) => (
            <div key={m.what} className="move"><span className="ok">✓</span><span>{m.what}</span><b className="num">{m.amount}</b><span className="sub">{m.sub}</span></div>
          ))}
        </div>
      )}
    </>
  )
}
