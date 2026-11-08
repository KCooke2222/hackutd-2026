import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { usd } from '../../lib/api'
import type { TrackLevel, TrackPoint, Tracking } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'
import { C } from '../../lib/chart'
import { ArrowRight, Check as CheckIcon, Circle } from 'lucide-react'

const LEVEL: Record<TrackLevel | 'good', [string, string]> = {
  on_track: ['On track', 'good'], drifting: ['Drifting', 'warn'], off_track: ['Off track', 'red'], good: ['Ahead', 'good'],
}
const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `$${Math.round(v)}`)
const day = (d: string, year = true) => new Date(d + 'T12:00:00').toLocaleDateString([], { month: 'short', day: 'numeric', ...(year ? { year: 'numeric' } : {}) })

/** Month 0 is the plan's starting state (the approve date), not the data's as-of month. */
function labels(t: Tracking) {
  const m0 = t.path[0]?.month
  const d0 = t.start?.date ?? (t.proposal.decided_at ?? t.proposal.created_at).slice(0, 10)
  return {
    m0, d0,
    long: (m: string | null | undefined) => (m && m === m0 ? day(d0) : ym(m)),
    tick: (m: string) => (m === 'before' ? 'Before' : m === m0 ? day(d0, false) : ymShort(m)),
  }
}

/** Home once a plan is approved: one goal (high-cost debt $0 by a month) and how the months are tracking against it. */
export default function Dashboard({ t, onReplan, onSnapshot }: {
  cid: string; t: Tracking; onChose: (proposalId: string) => void; onReplan: () => void; onSnapshot: () => void
}) {
  const st = t.status
  const L = labels(t)
  const [pill, cls] = st.goal_reached && st.level === 'on_track' ? ['Goal reached', 'good'] : LEVEL[st.level]
  const ahead = !st.goal_reached && st.late_months < 0
  const k = t.month_index
  // Lump sums at approve already took high-cost debt to $0: the job now is keeping it there.
  const maintain = st.goal_reached && t.goal.month === L.m0
  const paid = t.start?.high_cost_debt ?? 0
  const left = t.debts.filter((d) => !d.high_cost && d.actual > 0.005).map((d) => d.name)
  return (
    <>
      <div className="card dash-goal">
        <div className="col-head">
          <span className="k">Goal · {t.goal.label}</span>
          <span key={`${k}-${st.level}`} className={`tag ${ahead ? 'good' : cls} pop`}>{ahead ? `${-st.late_months} mo ahead` : pill}</span>
        </div>
        {maintain ? (
          <div className="dash-dates">
            <div><span className="k">Reached</span><b className="good">{L.long(t.goal.month)}</b><span className="small muted">paid at approve</span></div>
            <div><span className="k">All debt</span><b>{usd(t.tiles.total_debt)}</b>{t.start && <span className="small muted">was {usd(t.start.total_debt)}</span>}</div>
            <div><span className="k">{k === 0 ? 'Plan starts' : 'Month'}</span><b>{k === 0 ? L.long(L.m0) : `${k} of ${t.horizon}`}</b></div>
          </div>
        ) : (
          <div className="dash-dates">
            <div><span className="k">Goal</span><b>{L.long(t.goal.month)}</b></div>
            <div><span className="k">Projected</span><b className={st.late_months > 0 ? 'bad' : 'good'}>{L.long(st.projected_month)}</b>
              {st.late_months !== 0 && st.projected_month && <span className="small muted">{Math.abs(st.late_months)} mo {st.late_months > 0 ? 'late' : 'early'}</span>}</div>
            <div><span className="k">{k === 0 ? 'Plan starts' : 'Month'}</span><b>{k === 0 ? L.long(L.m0) : `${k} of ${t.horizon}`}</b></div>
          </div>
        )}
        {maintain && paid > 0 && (
          <div className="small muted" style={{ marginTop: 8 }}>
            {usd(paid)} of high-cost debt paid off at approve. Now: keep the cards at $0 and spending on target{left.length ? `, while the ${left.join(' and the ')} pay${left.length > 1 ? '' : 's'} down on schedule` : ''}.
          </div>
        )}
        {(st.breaks.length > 0 || st.nudges.length > 0 || (st.level !== 'on_track' && st.reasons.length > 0)) && (
          <div key={k} className="dash-why pop">
            {st.breaks.map((b) => <div key={b} className="move"><span className="bad">!</span><span>{b}</span><span /></div>)}
            {st.level !== 'on_track' && st.reasons.map((r) => <div key={r} className="move"><span className="same">·</span><span>{r}</span><span /></div>)}
            {st.nudges.filter((n) => !(st.level !== 'on_track' && st.reasons.includes(n))).map((n) => <div key={n} className="move"><span className="ok" style={{ color: 'var(--warn-ink)' }}><ArrowRight size={14} strokeWidth={2} className="ic-inline" /></span><span>{n}</span><span /></div>)}
          </div>
        )}
      </div>

      {t.switch && (
        <div key={k} className="card switch pop-late">
          <div className="switch-opts">
            <div className="opt off">
              <span className="k">Old plan</span>
              <h3>Doesn't work anymore</h3>
              <div className="big num">{st.projected_month ? L.long(st.projected_month) : 'Never'}</div>
              <div className="small muted">High-cost debt $0 by then; the goal was {L.long(t.goal.month)}{st.projected_month && st.late_months > 0 ? ` (${st.late_months} mo late)` : ''}.</div>
            </div>
            <div className="opt">
              <span className="k">Replan</span>
              <h3>Find a new plan</h3>
              <div className="small muted">The agent reruns with your numbers as they are now.</div>
              <button className="primary" onClick={onReplan}>Replan</button>
            </div>
          </div>
        </div>
      )}

      <DebtChart t={t} />

      <div className="ledger">
        <Milestones t={t} maintain={maintain} />
        <Debts t={t} k={k} />
      </div>

      <Alerts t={t} />

      <div className="dash-tiles">
        <Tile label="Net worth" v={t.tiles.net_worth} exp={t.tiles.net_worth_expected} />
        <Tile label="Cash" v={t.tiles.cash} exp={t.tiles.cash_expected} />
        <Tile label="All debt" v={t.tiles.total_debt} exp={t.tiles.total_debt_expected} />
      </div>
      <div className="small muted" style={{ textAlign: 'right' }}>
        Tracking plan from {day(L.d0)} · <button className="ghost" onClick={onSnapshot}>Open snapshot</button>
      </div>
    </>
  )
}

function DebtChart({ t }: { t: Tracking }) {
  const k = t.month_index
  const L = labels(t)
  const tol = t.tolerance_pct / 100
  // High-cost debt already $0 everywhere (paid at approve): chart all debt, so the line still moves.
  const total = t.path.every((p) => p.expected <= 0.01 && (p.actual ?? 0) <= 0.01 && (p.projected ?? 0) <= 0.01)
  const pick = (p: TrackPoint) => (total
    ? { month: p.month, expected: p.expected_total, band: Math.round(p.expected_total * (1 + tol) * 100) / 100, actual: p.actual_total, projected: p.projected_total }
    : { month: p.month, expected: p.expected, band: p.band, actual: p.actual, projected: p.projected })
  // Show the stretch that matters: through the later of the goal and the projection, plus a few months.
  const idx = (m: string | null | undefined) => (m ? t.path.findIndex((p) => p.month === m) : -1)
  const proj = idx(t.status.projected_month)
  const end = total ? Math.min(t.path.length - 1, Math.max(12, k + 6))
    : proj < 0 ? t.path.length - 1 : Math.min(t.path.length - 1, Math.max(idx(t.goal.month), proj, k) + 4)
  const rows = t.path.slice(0, end + 1).map(pick)
  // Where it stood before approve, so the month-0 payoff shows as a drop instead of starting at the bottom.
  const before = t.start ? (total ? t.start.total_debt : t.start.high_cost_debt) : null
  const showBefore = before != null && rows.length > 0 && Math.abs(before - rows[0].expected) >= 1
  const data = [...(showBefore ? [{ month: 'before', expected: before, band: before, actual: before, projected: undefined }] : []), ...rows]
    .map((p) => ({ ...p, range: [p.expected, p.band] as [number, number] }))
  const cur = pick(t.path[k] ?? t.path[0])
  const goalAt0 = t.goal.month === L.m0
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{total ? 'All debt' : 'High-cost debt'}</h2>
        <span className="total">{usd(cur.actual ?? 0)} <span className="small muted">plan {usd(cur.expected)}</span></span>
      </div>
      {total && t.start && t.start.high_cost_debt > 0 && (
        <div className="small muted" style={{ margin: '-4px 0 6px' }}>
          High-cost debt went from {usd(t.start.high_cost_debt)} to $0 at approve; this tracks what's left.
        </div>
      )}
      <ResponsiveContainer width="100%" height={240}>
        <ComposedChart data={data} margin={{ top: 14, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={C.grid} vertical={false} />
          <XAxis dataKey="month" tickFormatter={L.tick} interval={data.length > 14 ? 2 : 0} tick={{ fontSize: 11, fill: C.tick }} tickLine={false} axisLine={{ stroke: C.grid }} />
          <YAxis tickFormatter={short} domain={[0, (max: number) => Math.max(max, 100)]} tick={{ fontSize: 11, fill: C.tick }} width={48} tickLine={false} axisLine={false} />
          <Tooltip labelFormatter={(m) => (m === 'before' ? 'Before approve' : L.long(String(m)))}
            formatter={(v, n) => (n === 'range' ? [`${usd((v as number[])[0])}–${usd((v as number[])[1])}`, `Plan +${t.tolerance_pct}%`] : [usd(Number(v)), NAMES[String(n)] ?? String(n)])} />
          <Area dataKey="range" stroke="none" fill="var(--accent-soft)" fillOpacity={1} isAnimationActive={false} />
          <Line dataKey="expected" stroke={C.plan} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
          <Line dataKey="projected" stroke={C.marker} strokeDasharray="2 4" strokeWidth={2} dot={false} connectNulls animationDuration={700} />
          <Line dataKey="actual" stroke={C.marker} strokeWidth={3} dot={{ r: 3, fill: C.marker, strokeWidth: 0 }} animationDuration={700} />
          {k > 0 && <ReferenceLine x={t.path[k].month} stroke={C.base} strokeDasharray="2 3" label={{ value: 'now', position: 'insideTopRight', fill: C.tick, fontSize: 11 }} />}
          {(!total || goalAt0) && <ReferenceLine x={t.goal.month} stroke={C.good} strokeOpacity={0.6}
            label={{ value: goalAt0 ? 'high-cost $0' : `goal ${ymShort(t.goal.month)}`, position: 'insideTopLeft', fill: C.good, fontSize: 11 }} />}
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

function Milestones({ t, maintain }: { t: Tracking; maintain: boolean }) {
  const L = labels(t)
  const k = t.month_index
  const rows = t.debts.filter((d) => d.high_cost && d.goal_month).sort((a, b) => (a.goal_month ?? '').localeCompare(b.goal_month ?? ''))
  const atApprove = (t.start?.paid_at_approve ?? []).filter((d) => d.high_cost)
  const nwOk = (t.goal.projected_net_worth_24m ?? 0) >= t.goal.net_worth_24m - 1
  const gain = t.start ? t.tiles.net_worth - t.start.net_worth : null
  const step = gain == null ? 0 : [1000, 2500, 5000, 10000, 25000, 50000].find((x) => x > gain) ?? 100000
  const hit = gain == null ? 0 : [1000, 2500, 5000, 10000, 25000, 50000].filter((x) => x <= gain).pop() ?? 0
  return (
    <div className="card">
      <h2>Milestones</h2>
      {atApprove.map((d) => <Check key={d.account_id} done late={false} label={`${d.name} $0`} goal={null} proj={null} doneText="at approve" />)}
      {rows.map((d) => <Check key={d.account_id} done={d.done} late={!d.done && d.late_months > 0} label={`${d.name} $0`} goal={L.long(d.goal_month)} proj={d.done ? null : d.projected_month && L.long(d.projected_month)} />)}
      <Check done={t.status.goal_reached} late={!t.status.goal_reached && t.status.late_months > 0} label="All high-cost debt $0" goal={L.long(t.goal.month)}
        proj={t.status.goal_reached ? null : t.status.projected_month && L.long(t.status.projected_month)} strong
        doneText={maintain ? 'at approve' : undefined} sub={maintain && k > 0 ? `still $0 after ${k} month${k > 1 ? 's' : ''}` : undefined} />
      {gain != null && k > 0 && (
        <>
          {hit > 0 && <Check done late={false} label={`Net worth up ${short(hit)} since approve`} goal={null} proj={null} doneText={usd(gain)} />}
          <Check done={false} late={false} label={`Net worth up ${short(step)} since approve`} goal={null} proj={null} sub={`${usd(Math.max(gain, 0))} so far`} />
        </>
      )}
      <div className="move dim">
        <span className={nwOk ? 'ok' : 'same'}>{nwOk ? <CheckIcon size={14} strokeWidth={2.5} className="ic-inline" /> : <Circle size={14} strokeWidth={2} className="ic-inline" />}</span>
        <span className="muted">Net worth in 24 mo ≥ {usd(t.goal.net_worth_24m)}</span>
        <span className="small muted num">{usd(t.goal.projected_net_worth_24m)}</span>
      </div>
    </div>
  )
}

function Check({ done, late, label, goal, proj, strong, doneText, sub }: {
  done: boolean; late: boolean; label: string; goal: string | null; proj: string | null | undefined; strong?: boolean; doneText?: string; sub?: string
}) {
  return (
    <div className="move">
      <span className={done ? 'ok' : late ? 'bad' : 'same'}>{done ? <CheckIcon size={14} strokeWidth={2.5} className="ic-inline" /> : <Circle size={14} strokeWidth={2} className="ic-inline" />}</span>
      <span>{strong ? <b>{label}</b> : label}</span>
      <span className="small num">{done ? <span className="good">{doneText ?? 'done'}</span> : <>{goal}</>}</span>
      {sub ? <span className="sub">{sub}</span> : !done && proj && proj !== goal && <span className="sub">{late ? 'now' : 'now ahead:'} {proj}</span>}
    </div>
  )
}

function Debts({ t, k }: { t: Tracking; k: number }) {
  const L = labels(t)
  return (
    <div className="card">
      <div className="col-head"><h2 style={{ margin: 0 }}>Each debt</h2><span className="small muted">{k === 0 ? 'today' : 'vs plan this month'}</span></div>
      {(t.start?.paid_at_approve ?? []).map((d) => (
        <div key={d.account_id} className="lrow">
          <span className="name">{d.name}</span>
          <span className="amt"><span className="was">{usd(d.balance)}</span><span>$0</span></span>
          <span className="sub"><span className="tag">{d.apr_pct}%</span><span className="tag good">paid at approve</span></span>
        </div>
      ))}
      {t.debts.map((d) => {
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
              {!d.done && d.projected_month && <span className="tag">$0 {L.long(d.projected_month)}</span>}
            </span>
          </div>
        )
      })}
      <div className="small muted" style={{ marginTop: 6 }}>Struck-through: {t.start?.paid_at_approve.length ? 'the balance before approve, or ' : ''}where the plan has it this month.</div>
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
            <span className="small muted num">{a.from_month ? `${ymShort(a.from_month)}–${ymShort(a.month)}` : ym(a.month)}</span>
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
