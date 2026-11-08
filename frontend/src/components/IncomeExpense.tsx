import { useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../lib/api'

type Month = {
  month: string; income: number; essentials: number; discretionary: number; debt_payments: number
  savings_invest: number; transfers_out_unknown: number; surplus: number
}
type CashFlow = { months: Month[]; monthly_avg: Month; unknown_outflow_per_month: number }

const SERIES = [
  { key: 'essentials', name: 'Essentials', color: '#6b7280' },
  { key: 'discretionary', name: 'Discretionary', color: '#fb923c' },
  { key: 'debt_payments', name: 'Debt payoff', color: '#2563eb' },
  { key: 'savings_invest', name: 'Saved', color: '#0d9488' },
  { key: 'transfers_out_unknown', name: 'Unknown outflow', color: '#dc2626' },
] as const

const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`)
const mon = (m: string) => new Date(`${m}-15`).toLocaleString('en-US', { month: 'short' })

/** Income vs where it went, last 12 months (insights.cash_flow). */
export default function IncomeExpense({ cid }: { cid: string }) {
  const [cf, setCf] = useState<CashFlow | null>(null)
  useEffect(() => {
    setCf(null)
    api<CashFlow>(`/customers/${cid}/cashflow?months=12`).then(setCf).catch(() => setCf(null))
  }, [cid])
  if (!cf) return null
  const a = cf.monthly_avg
  // Stacked bars can't go below 0: a month where the card grew shows 0 debt payoff.
  const data = cf.months.map((m) => ({ ...m, debt_payments: Math.max(0, m.debt_payments), label: mon(m.month) }))

  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Income vs spending</h2>
        <div className="legend">
          <span><i style={{ borderColor: '#111827' }} />Income</span>
          <span><i className="dash" style={{ borderColor: '#16a34a' }} />Left over</span>
          {SERIES.map((s) => <span key={s.key}><i style={{ borderColor: s.color }} />{s.name}</span>)}
        </div>
      </div>
      <div className="outs">
        <div><div className="k">Income / mo</div><div className="v">{usd(a.income)}</div></div>
        <div><div className="k">Unknown outflow / mo</div><div className="v bad">{usd(cf.unknown_outflow_per_month)}</div></div>
        <div><div className="k">Debt payoff / mo</div><div className="v">{usd(a.debt_payments)}</div></div>
        <div><div className="k">Left over / mo</div><div className={`v ${a.surplus > 0 ? '' : 'bad'}`}>{usd(a.surplus)}</div></div>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#e5e7eb" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#6b7280' }} tickLine={false} axisLine={{ stroke: '#e5e7eb' }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: '#6b7280' }} width={44} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v, name) => [usd(Number(v)), name]} />
          {SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} stackId="out" fill={s.color} isAnimationActive={false} />
          ))}
          <Line dataKey="income" name="Income" stroke="#111827" strokeWidth={2.5} dot={false} type="linear" isAnimationActive={false} />
          <Line dataKey="surplus" name="Left over" stroke="#16a34a" strokeDasharray="5 4" strokeWidth={2} dot={false} type="linear" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
