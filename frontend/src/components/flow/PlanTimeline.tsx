import { useEffect, useState } from 'react'
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../../lib/api'
import type { PlanV2, Timeline, TimelineAccount, TimelineChange } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'
import CashflowAhead from './CashflowAhead'
import { GREY } from './NetWorth'

const DEBT_COLORS = ['var(--d1)', 'var(--d2)', 'var(--d3)', 'var(--d4)', 'var(--d5)', 'var(--d6)']
const ASSET_COLORS = ['var(--a1)', 'var(--a2)', 'var(--a3)', 'var(--a4)', 'var(--muted)', 'var(--a6)']
const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`)

/** The home page again, but over time: accounts by month, payments, income vs spending, every change. */
export default function PlanTimeline({ cid, plan }: { cid: string; plan: PlanV2 }) {
  const [t, setT] = useState<Timeline | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [m, setM] = useState(12)
  const key = JSON.stringify(plan)
  useEffect(() => {
    const id = setTimeout(() => {
      api<Timeline>(`/customers/${cid}/plan/timeline`, { method: 'POST', body: JSON.stringify({ plan }) })
        .then((x) => { setT(x); setErr(null) })
        .catch((e) => setErr(String(e.message ?? e)))
    }, 300)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, key])
  if (err && !t) return <div className="card error">{err}</div>
  if (!t) return null
  const H = t.months.length - 1
  const at = Math.min(m, H)
  const label = at === 0 ? 'Now' : ym(t.months[at])

  return (
    <>
      <h2 style={{ margin: '24px 0 0' }}>What the plan does, month by month</h2>
      <div className="card scrub">
        <span className="when">{label}</span>
        <input type="range" aria-label="month" min={0} max={H} step={1} value={at} onChange={(e) => setM(+e.target.value)} />
        <span className="small muted">{at === 0 ? 'today' : `${at} mo from now`}</span>
      </div>
      <Accounts t={t} at={at} />
      <Payments t={t} at={at} />
      <CashflowAhead cid={cid} t={t} at={at} />
      <Changes changes={t.changes} />
    </>
  )
}

function Accounts({ t, at }: { t: Timeline; at: number }) {
  const debts = t.accounts.filter((a) => a.side === 'debt')
  const assets = t.accounts.filter((a) => a.side === 'asset')
  return (
    <div className="ledger">
      <Col title="Have" accounts={assets} colors={ASSET_COLORS} t={t} at={at} />
      <Col title="Owe" accounts={debts} colors={DEBT_COLORS} t={t} at={at} />
    </div>
  )
}

function Col({ title, accounts, colors, t, at }: { title: string; accounts: TimelineAccount[]; colors: string[]; t: Timeline; at: number }) {
  const [hover, setHover] = useState<string | null>(null)
  const total = accounts.reduce((s, a) => s + a.plan[at], 0)
  const totalBefore = accounts.reduce((s, a) => s + a.before[at], 0)
  const data = t.months.map((mo, i) => {
    const row: Record<string, number | string> = { m: mo }
    for (const a of accounts) { row[a.id] = a.plan[i]; row[`${a.id}~`] = a.before[i] }
    return row
  })
  const better = title === 'Owe' ? total < totalBefore - 0.5 : total > totalBefore + 0.5
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>{title}</h2>
        <span className="total">
          {Math.abs(total - totalBefore) >= 1 && <span className="was">{usd(totalBefore)}</span>}
          <span className={better ? 'good' : ''}>{usd(total)}</span>
        </span>
      </div>
      {accounts.map((a, i) => {
        const v = a.plan[at], b = a.before[at]
        const tags: [string, string?][] = []
        if (a.apr_pct != null) tags.push([`${a.apr_pct}%`])
        if (a.side === 'debt' && v <= 0.005 && a.plan[0] > 0.005) tags.push([`paid off ${ym(a.payoff_plan)}`, 'good'])
        else if (a.side === 'debt' && a.payoff_plan && at === 0) tags.push([`$0 by ${ym(a.payoff_plan)}`, 'good'])
        if (Math.abs(v - b) >= 1) tags.push([`without the plan ${usd(b)}`])
        return (
          <div key={a.id} className={`lrow ${hover === a.id ? 'hl' : ''}`} onMouseEnter={() => setHover(a.id)} onMouseLeave={() => setHover(null)}>
            <span className="name"><i className="dot" style={{ background: colors[i % colors.length] }} />{a.name}</span>
            <span className="amt">{usd(v)}</span>
            {tags.length > 0 && <span className="sub">{tags.map(([x, c]) => <span key={x} className={`tag ${c ?? ''}`}>{x}</span>)}</span>}
          </div>
        )
      })}
      <ResponsiveContainer width="100%" height={150}>
        <LineChart data={data} margin={{ top: 12, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="m" tickFormatter={ymShort} interval={5} tick={{ fontSize: 10, fill: 'var(--muted)' }} tickLine={false} axisLine={{ stroke: 'var(--line)' }} />
          <YAxis tickFormatter={short} tick={{ fontSize: 10, fill: 'var(--muted)' }} width={40} tickLine={false} axisLine={false} />
          <Tooltip formatter={(v, n) => [usd(Number(v)), nameOf(accounts, String(n))]} labelFormatter={(mo) => ym(String(mo))} />
          <ReferenceLine x={t.months[at]} stroke="var(--text)" strokeDasharray="2 3" />
          {hover && <Line dataKey={`${hover}~`} stroke={GREY} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />}
          {accounts.map((a, i) => (
            <Line key={a.id} dataKey={a.id} stroke={colors[i % colors.length]} strokeWidth={hover === a.id ? 3 : 2}
              strokeOpacity={hover && hover !== a.id ? 0.35 : 1} dot={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <div className="legend"><span><i style={{ borderColor: 'var(--text)' }} />Plan</span><span><i className="dash" style={{ borderColor: GREY }} />Without the plan (hover a row)</span></div>
    </div>
  )
}

const nameOf = (accounts: TimelineAccount[], key: string) => {
  const a = accounts.find((x) => x.id === key.replace(/~$/, ''))
  return a ? `${a.name}${key.endsWith('~') ? ' (without plan)' : ''}` : key
}

function Payments({ t, at }: { t: Timeline; at: number }) {
  const i = Math.max(at, 1) - 1
  const total = t.payments.reduce((s, p) => s + p.plan[i], 0)
  const totalBefore = t.payments.reduce((s, p) => s + p.before[i], 0)
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Paid to each debt, {ym(t.months[i + 1])}</h2>
        <span className="total"><span className="was">{usd(totalBefore)}</span>{usd(total)}/mo</span>
      </div>
      {t.payments.map((p) => {
        const v = p.plan[i], b = p.before[i]
        const tags: [string, string?][] = []
        if (p.lump_sum && i === 0) tags.push([`+ ${usd(p.lump_sum)} once`, 'good'])
        if (p.extra_rank) tags.push([`extra money #${p.extra_rank}`])
        else if (Math.abs(v - p.min_payment) < 1) tags.push(['minimum'])
        if (p.payoff_plan && v < 1 && (t.accounts.find((a) => a.id === p.account_id)?.plan[i + 1] ?? 1) <= 0.005) tags.push(['paid off', 'good'])
        else if (p.autopay && p.payoff_plan && t.months.indexOf(p.payoff_plan) <= i) tags.push(['statement in full', 'good'])
        if (p.card_charges) tags.push([`covers ~${usd(p.card_charges.plan)} new card spending`])
        return (
          <div key={p.account_id} className="lrow">
            <span className="name">{p.name}</span>
            <span className="amt">{Math.abs(v - b) >= 1 && <span className="was">{usd(b)}</span>}{usd(v)}</span>
            <span className="sub">{tags.map(([x, c]) => <span key={x} className={`tag ${c ?? ''}`}>{x}</span>)}</span>
          </div>
        )
      })}
      <div className="small muted" style={{ marginTop: 6 }}>Struck-through: what you pay now in the same month.</div>
    </div>
  )
}

const GROUPS = ['Debts', 'Money out', 'Spending', 'Income', 'Saving']

function Changes({ changes }: { changes: TimelineChange[] }) {
  const groups = [...GROUPS, ...new Set(changes.map((c) => c.group).filter((g) => !GROUPS.includes(g)))]
  const n = changes.filter((c) => c.changed).length
  return (
    <div className="card">
      <div className="col-head"><h2 style={{ margin: 0 }}>What changes, what stays</h2><span className="small muted">{n} changes · {changes.length - n} stay the same</span></div>
      {groups.map((g) => {
        const rows = changes.filter((c) => c.group === g)
        if (!rows.length) return null
        return (
          <div key={g}>
            <div className="group-label">{g}</div>
            {rows.map((c) => (
              <div key={c.label} className="move">
                {c.changed ? <span className="ok" style={{ color: 'var(--accent)' }}>→</span> : <span className="same">=</span>}
                <span><b>{c.label}</b>: <span className={c.changed ? '' : 'muted'}>{c.text}</span></span>
                <span />
                {c.sub && <span className="sub">{c.sub}</span>}
              </div>
            ))}
          </div>
        )
      })}
      <div className="small muted" style={{ marginTop: 6 }}>
        <span style={{ color: 'var(--accent)' }}>→</span> changes under the plan · = keep doing what you do now
      </div>
    </div>
  )
}
