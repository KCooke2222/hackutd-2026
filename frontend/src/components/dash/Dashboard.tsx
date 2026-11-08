import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usd } from '../../lib/api'
import type { TrackDebt, TrackLevel, Tracking } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'
import { C } from '../../lib/chart'
import { ArrowRight, Check as CheckIcon, Circle } from 'lucide-react'
import SwitchPlan from './SwitchPlan'

const LEVEL: Record<TrackLevel | 'good', [string, string]> = {
  on_track: ['On track', 'good'], drifting: ['Drifting', 'warn'], off_track: ['Off track', 'red'], good: ['Ahead', 'good'],
}
const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `$${Math.round(v)}`)

/** Home once a plan is approved: one goal (high-cost debt $0 by a month) and how the months are tracking against it. */
export default function Dashboard({ cid, t, onChose, onReplan, onSnapshot }: {
  cid: string; t: Tracking; onChose: (proposalId: string) => void; onReplan: () => void; onSnapshot: () => void
}) {
  const st = t.status
  const [pill, cls] = st.goal_reached ? ['Goal reached', 'good'] : LEVEL[st.level]
  const ahead = !st.goal_reached && st.late_months < 0
  const k = t.month_index
  return (
    <>
      <div className="card dash-goal">
        <div className="col-head">
          <span className="k">Goal · {t.goal.label}</span>
          <span key={`${k}-${st.level}`} className={`tag ${ahead ? 'good' : cls} pop`}>{ahead ? `${-st.late_months} mo ahead` : pill}</span>
        </div>
        <div className="dash-dates">
          <div><span className="k">Goal</span><b>{ym(t.goal.month)}</b></div>
          <div><span className="k">Projected</span><b className={st.late_months > 0 ? 'bad' : 'good'}>{ym(st.projected_month)}</b>
            {st.late_months !== 0 && st.projected_month && <span className="small muted">{Math.abs(st.late_months)} mo {st.late_months > 0 ? 'late' : 'early'}</span>}</div>
          <div><span className="k">{k === 0 ? 'Plan starts' : 'Month'}</span><b>{k === 0 ? ym(t.now) : `${k} of ${t.horizon}`}</b></div>
        </div>
        {(st.breaks.length > 0 || st.nudges.length > 0 || (st.level !== 'on_track' && st.reasons.length > 0)) && (
          <div key={k} className="dash-why pop">
            {st.breaks.map((b) => <div key={b} className="move"><span className="bad">!</span><span>{b}</span><span /></div>)}
            {st.level !== 'on_track' && st.reasons.map((r) => <div key={r} className="move"><span className="same">·</span><span>{r}</span><span /></div>)}
            {st.nudges.map((n) => <div key={n} className="move"><span className="ok" style={{ color: 'var(--warn-ink)' }}><ArrowRight size={14} strokeWidth={2} className="ic-inline" /></span><span>{n}</span><span /></div>)}
          </div>
        )}
      </div>

      {t.switch && <SwitchPlan key={k} cid={cid} sw={t.switch} onChose={onChose} onReplan={onReplan} />}

      <DebtChart t={t} />

      <div className="ledger">
        <Milestones t={t} />
        <Debts debts={t.debts} k={k} />
      </div>

      <Alerts t={t} />

      <div className="dash-tiles">
        <Tile label="Net worth" v={t.tiles.net_worth} exp={t.tiles.net_worth_expected} />
        <Tile label="Cash" v={t.tiles.cash} exp={t.tiles.cash_expected} />
        <Tile label="All debt" v={t.tiles.total_debt} exp={t.tiles.total_debt_expected} />
      </div>
      <div className="small muted" style={{ textAlign: 'right' }}>
        Tracking plan from {new Date(t.proposal.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })} · <button className="ghost" onClick={onSnapshot}>Open snapshot</button>
      </div>
    </>
  )
}

function DebtChart({ t }: { t: Tracking }) {
  const k = t.month_index
  // Show the stretch that matters: through the later of the goal and the projection, plus a few months.
  const idx = (m: string | null | undefined) => (m ? t.path.findIndex((p) => p.month === m) : -1)
  const proj = idx(t.status.projected_month)
  const end = proj < 0 ? t.path.length - 1 : Math.min(t.path.length - 1, Math.max(idx(t.goal.month), proj, k) + 4)
  const data = t.path.slice(0, end + 1).map((p) => ({ ...p, range: [p.expected, p.band] as [number, number] }))
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>High-cost debt</h2>
        <span className="total">{usd(t.path[k]?.actual ?? 0)} <span className="small muted">plan {usd(t.path[k]?.expected ?? 0)}</span></span>
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 14, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="month" tickFormatter={ymShort} interval={end > 12 ? 2 : 0} tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: C.tick }} width={48} tickLine={false} axisLine={false} />
          <Tooltip labelFormatter={(m) => ym(String(m))}
            formatter={(v, n) => (n === 'range' ? [`${usd((v as number[])[0])}–${usd((v as number[])[1])}`, `Plan +${t.tolerance_pct}%`] : [usd(Number(v)), NAMES[String(n)] ?? String(n)])} />
          <Area dataKey="range" stroke="none" fill="var(--accent-soft)" fillOpacity={1} isAnimationActive={false} />
          <Line dataKey="expected" stroke={C.plan} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="projected" stroke={C.marker} strokeDasharray="2 4" strokeWidth={2} dot={false} connectNulls animationDuration={700} />
          <Line dataKey="actual" stroke={C.marker} strokeWidth={3} dot={{ r: 3, fill: C.marker, strokeWidth: 0 }} animationDuration={700} />
          {k > 0 && <ReferenceLine x={t.path[k].month} stroke={C.base} strokeDasharray="2 3" label={{ value: 'now', position: 'insideTopRight', fill: C.tick, fontSize: 11 }} />}
          <ReferenceLine x={t.goal.month} stroke={C.good} strokeOpacity={0.6} label={{ value: `goal ${ymShort(t.goal.month)}`, position: 'insideTopLeft', fill: C.good, fontSize: 11 }} />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="legend">
        <span><i style={{ borderColor: C.marker }} />Actual</span>
        <span><i className="dash" style={{ borderColor: C.marker }} />Projected from here</span>
        <span><i className="dash" style={{ borderColor: C.plan }} />Plan</span>
        <span><i style={{ borderColor: 'var(--accent-soft)', borderTopWidth: 8 }} />Within {t.tolerance_pct}% of plan</span>
      </div>
    </div>
  )
}
const NAMES: Record<string, string> = { expected: 'Plan', actual: 'Actual', projected: 'Projected' }

function Milestones({ t }: { t: Tracking }) {
  const rows = t.debts.filter((d) => d.high_cost && d.goal_month).sort((a, b) => (a.goal_month ?? '').localeCompare(b.goal_month ?? ''))
  const nwOk = (t.goal.projected_net_worth_24m ?? 0) >= t.goal.net_worth_24m - 1
  return (
    <div className="card">
      <h2>Milestones</h2>
      {rows.map((d) => <Check key={d.account_id} done={d.done} late={!d.done && d.late_months > 0} label={`${d.name} $0`} goal={d.goal_month} proj={d.done ? null : d.projected_month} />)}
      <Check done={t.status.goal_reached} late={!t.status.goal_reached && t.status.late_months > 0} label="All high-cost debt $0" goal={t.goal.month} proj={t.status.goal_reached ? null : t.status.projected_month} strong />
      <div className="move dim">
        <span className={nwOk ? 'ok' : 'same'}>{nwOk ? <CheckIcon size={14} strokeWidth={2.5} className="ic-inline" /> : <Circle size={14} strokeWidth={2} className="ic-inline" />}</span>
        <span className="muted">Net worth in 24 mo ≥ {usd(t.goal.net_worth_24m)}</span>
        <span className="small muted num">{usd(t.goal.projected_net_worth_24m)}</span>
      </div>
    </div>
  )
}

function Check({ done, late, label, goal, proj, strong }: { done: boolean; late: boolean; label: string; goal: string | null; proj: string | null; strong?: boolean }) {
  return (
    <div className="move">
      <span className={done ? 'ok' : late ? 'bad' : 'same'}>{done ? <CheckIcon size={14} strokeWidth={2.5} className="ic-inline" /> : <Circle size={14} strokeWidth={2} className="ic-inline" />}</span>
      <span>{strong ? <b>{label}</b> : label}</span>
      <span className="small num">{done ? <span className="good">done</span> : <>{ym(goal)}</>}</span>
      {!done && proj && proj !== goal && <span className="sub">{late ? 'now' : 'now ahead:'} {ym(proj)}</span>}
    </div>
  )
}

function Debts({ debts, k }: { debts: TrackDebt[]; k: number }) {
  return (
    <div className="card">
      <div className="col-head"><h2 style={{ margin: 0 }}>Each debt</h2><span className="small muted">{k === 0 ? 'today' : 'vs plan this month'}</span></div>
      {debts.map((d) => {
        const diff = d.actual - d.expected
        const over = diff > Math.max(25, 0.1 * d.expected)
        return (
          <div key={d.account_id} className={`lrow ${d.high_cost ? '' : 'dim'}`}>
            <span className="name">{d.name}</span>
            <span className="amt">{Math.abs(diff) >= 1 && <span className="was">{usd(d.expected)}</span>}<span className={over ? 'bad' : ''}>{usd(d.actual)}</span></span>
            <span className="sub">
              <span className="tag">{d.apr_pct}%</span>
              {d.done ? <span className="tag good">paid off</span>
                : !d.high_cost ? <span className="tag">minimums</span>
                : Math.abs(diff) < 1 ? <span className="tag good">on plan</span>
                : <span className={`tag ${over ? 'red' : diff > 0 ? 'warn' : 'good'}`}>{diff > 0 ? '+' : '−'}{usd(Math.abs(diff))} vs plan</span>}
              {!d.done && d.projected_month && <span className="tag">$0 {ym(d.projected_month)}</span>}
            </span>
          </div>
        )
      })}
      <div className="small muted" style={{ marginTop: 6 }}>Struck-through: where the plan has it this month.</div>
    </div>
  )
}

function Alerts({ t }: { t: Tracking }) {
  return (
    <div className="card">
      <h2>Alerts</h2>
      {t.alerts.length === 0 && <div className="small muted">Nothing yet. Each month gets a check against the plan.</div>}
      {t.alerts.map((a, i) => {
        const [, cls] = LEVEL[a.level] ?? LEVEL.on_track
        return (
          <div key={a.month_index} className={`alert ${i === 0 ? 'pop' : ''}`}>
            <span className={`tag ${cls}`}>{a.title}</span>
            <span>{a.text}</span>
            <span className="small muted num">{ym(a.month)}</span>
          </div>
        )
      })}
    </div>
  )
}

function Tile({ label, v, exp }: { label: string; v: number; exp: number }) {
  return (
    <div>
      <span className="k">{label}</span>
      <b className="num">{usd(v)}</b>
      {Math.abs(v - exp) >= 1 && <span className="small muted">plan {usd(exp)}</span>}
    </div>
  )
}
