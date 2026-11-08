import { useEffect, useState } from 'react'
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../lib/api'
import { C, FLOW_SERIES, INK, short } from '../lib/chart'

type Month = {
  month: string; income: number; essentials: number; discretionary: number; debt_payments: number
  savings_invest: number; surplus: number
}
type CashFlow = { months: Month[]; monthly_avg: Month }

const mon = (m: string) => new Date(`${m}-15`).toLocaleString('en-US', { month: 'short' })

/** Income vs where it went, last 12 months (insights.cash_flow). The coloured averages double as the key. */
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
  // Left over = what stays in cash (the change in PNC cash), so it is the "saved" number too.
  const stats = [
    { k: 'Income', v: a.income, color: C.income, ink: INK.income, line: 'solid' },
    ...FLOW_SERIES.map((s) => ({ k: s.name, v: a[s.key], color: s.color, ink: s.ink, line: 'bar' })),
    { k: 'Left over', v: a.surplus, color: C.leftOver, ink: INK.leftOver, line: 'dash' },
  ]

  return (
    <div className="card">
      <h2>Income vs spending <span className="small muted" style={{ fontWeight: 400 }}>· monthly average, last 12 months</span></h2>
      <div className="outs keyed">
        {stats.map((s) => (
          <div key={s.k}>
            <div className="k"><i className={s.line} style={{ borderColor: s.color }} />{s.k}</div>
            <div className="v" style={{ color: s.ink }}>{usd(s.v)}</div>
          </div>
        ))}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 16, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: C.tick }} width={44} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v, name) => [usd(Number(v)), name]} />
          {FLOW_SERIES.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.name} stackId="out" fill={s.color} isAnimationActive={false} />
          ))}
          <Line dataKey="income" name="Income" stroke={C.income} strokeWidth={2.5} dot={false} type="linear" isAnimationActive={false} />
          <Line dataKey="surplus" name="Left over" stroke={C.leftOver} strokeDasharray="5 4" strokeWidth={2} dot={false} type="linear" isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
