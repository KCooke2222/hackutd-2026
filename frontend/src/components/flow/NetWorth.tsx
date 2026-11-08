import { CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usd } from '../../lib/api'
import type { SimV2 } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'

export const GREY = 'var(--grey)', ACCENT = 'var(--accent)', GOOD = 'var(--good)', BAD = 'var(--bad)'

/** Net worth for 24 months: the plan vs doing nothing (both from wealth.simulate_plan). */
export default function NetWorth({ sim, baseline, height = 240 }: { sim: SimV2; baseline: SimV2 | null; height?: number }) {
  const data = sim.months.map((m, i) => ({ m: m.month, plan: m.net_worth, base: baseline?.months[i]?.net_worth ?? null, high: m.high_cost_debt }))
  const freeAt = sim.high_cost_debt_free ? data.find((d) => d.m === sim.high_cost_debt_free) : null
  const gain = sim.net_worth_24m - sim.baseline_net_worth_24m
  const last = data[data.length - 1]
  const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`)
  return (
    <>
      <div className="col-head">
        <div className="legend">
          <span><i style={{ borderColor: ACCENT }} />Plan</span>
          <span><i className="dash" style={{ borderColor: GREY }} />Do nothing</span>
        </div>
        <b className={gain >= 0 ? 'good' : 'bad'} style={{ fontVariantNumeric: 'tabular-nums' }}>{gain >= 0 ? '+' : ''}{usd(gain)} in 24 mo</b>
      </div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 18, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="m" tickFormatter={ymShort} interval={5} tick={{ fontSize: 11, fill: 'var(--muted)' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: 'var(--muted)' }} width={48} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
          <Tooltip formatter={(v, n) => [usd(Number(v)), n === 'plan' ? 'Plan' : 'Do nothing']} labelFormatter={(m) => ym(String(m))} />
          <Line dataKey="base" stroke={GREY} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="plan" stroke={ACCENT} strokeWidth={3} dot={false} isAnimationActive={false} />
          {freeAt && (
            <ReferenceDot x={freeAt.m} y={freeAt.plan} r={5} fill={GOOD} stroke="var(--panel)"
              label={{ value: `High-cost debt $0 · ${ym(freeAt.m)}`, fill: GOOD, fontSize: 12, fontWeight: 700, position: 'top' }} />
          )}
          {last && <ReferenceDot x={last.m} y={last.plan} r={0} label={{ value: usd(last.plan), fill: ACCENT, fontSize: 12, fontWeight: 700, position: 'insideTopRight' }} />}
        </LineChart>
      </ResponsiveContainer>
    </>
  )
}
