import { useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../../lib/api'
import type { FlowMonth, Timeline } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'

type Past = { months: (Omit<FlowMonth, 'transfers_out'> & { transfers_out_unknown: number })[] }

const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`)

/** IncomeExpense, extended: the last 12 months as they were, then 24 months ahead (plan or as now). */
export default function CashflowAhead({ cid, t, at }: { cid: string; t: Timeline; at: number }) {
  const [past, setPast] = useState<Past | null>(null)
  const [view, setView] = useState<'plan' | 'before'>('plan')
  useEffect(() => {
    api<Past>(`/customers/${cid}/cashflow?months=12`).then(setPast).catch(() => setPast(null))
  }, [cid])
  const out = t.cashflow.transfers_out_name
  const SERIES = [
    { key: 'essentials', name: 'Essentials', color: 'var(--muted)' },
    { key: 'discretionary', name: 'Discretionary', color: '#fb923c' },
    { key: 'debt_payments', name: 'Debt payoff', color: '#2563eb' },
    { key: 'savings_invest', name: 'Saved', color: '#0d9488' },
    { key: 'transfers_out', name: out, color: '#dc2626' },
  ] as const
  const ahead = t.cashflow[view]
  const data = [
    ...(past?.months ?? []).map((m) => ({ ...m, transfers_out: m.transfers_out_unknown, debt_payments: Math.max(0, m.debt_payments), past: true })),
    ...ahead.map((m) => ({ ...m, past: false })),
  ]
  const i = Math.max(at, 1) - 1
  const b = t.cashflow.before[i], p = t.cashflow.plan[i]
  const spend = (x: FlowMonth) => x.essentials + x.discretionary
  const cells = [
    { k: 'Income', from: b.income, to: p.income },
    { k: 'Spending', from: spend(b), to: spend(p) },
    { k: out, from: b.transfers_out, to: p.transfers_out },
    { k: 'Debt payoff', from: b.debt_payments, to: p.debt_payments },
    { k: 'Saved', from: b.savings_invest, to: p.savings_invest },
    { k: 'Left over', from: b.surplus, to: p.surplus },
  ]
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Income vs spending, now and ahead</h2>
        <span className="seg">
          <button className={view === 'plan' ? 'on' : ''} onClick={() => setView('plan')}>With the plan</button>
          <button className={view === 'before' ? 'on' : ''} onClick={() => setView('before')}>As now</button>
        </span>
      </div>
      <div className="ba">
        {cells.map((c) => (
          <div key={c.k}>
            <div className="k">{c.k}, {ym(p.month)}</div>
            {Math.abs(c.to - c.from) >= 1 ? <div className="from">{usd(c.from)}</div> : <div className="from" style={{ textDecoration: 'none' }}>no change</div>}
            <div className="to">{usd(c.to)}</div>
          </div>
        ))}
      </div>
      <div className="legend" style={{ marginTop: 10 }}>
        <span><i style={{ borderColor: 'var(--text)' }} />Income</span>
        <span><i className="dash" style={{ borderColor: '#16a34a' }} />Left over</span>
        {SERIES.map((s) => <span key={s.key}><i style={{ borderColor: s.color }} />{s.name}</span>)}
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="month" tickFormatter={ymShort} interval={5} tick={{ fontSize: 11, fill: 'var(--muted)' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: 'var(--muted)' }} width={44} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v, name) => [usd(Number(v)), name]} labelFormatter={(mo) => ym(String(mo))} />
          {SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} stackId="out" fill={s.color} isAnimationActive={false} />
          ))}
          <Line dataKey="income" name="Income" stroke="var(--text)" strokeWidth={2.5} dot={false} type="linear" isAnimationActive={false} />
          <Line dataKey="surplus" name="Left over" stroke="#16a34a" strokeDasharray="5 4" strokeWidth={2} dot={false} type="linear" isAnimationActive={false} />
          {ahead[0] && <ReferenceLine x={ahead[0].month} stroke="var(--grey)" label={{ value: 'ahead →', position: 'insideTopLeft', fontSize: 11, fill: 'var(--muted)' }} />}
          <ReferenceLine x={p.month} stroke="var(--text)" strokeDasharray="2 3" />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="small muted">Left of the line: your last 12 months. Ahead: income and spending held at today’s averages. As on the home page, card payments that cover new card spending count as spending.</div>
    </div>
  )
}
