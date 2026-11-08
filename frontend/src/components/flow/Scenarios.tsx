import { useEffect, useRef, useState } from 'react'
import { Area, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, usd } from '../../lib/api'
import type { PlanV2, ScenarioEvent, ScenarioRange, ScenarioRun, ScenarioSpec, Verdict } from '../../lib/api'
import { ym, ymShort } from '../../lib/flow'
import { ACCENT, BAD, GOOD, GREY } from './NetWorth'

type Custom = { jobMonths: number; incomePct: number; expense: number; expenseMonth: number }
const NONE: Custom = { jobMonths: 0, incomePct: 0, expense: 0, expenseMonth: 1 }

const PRESETS: ScenarioSpec[] = [
  { id: 'job_loss_3m', label: 'Lose your job for 3 months', events: [{ kind: 'job_loss', start: 1, months: 3 }] },
  { id: 'income_drop_20', label: 'Income drops 20%', events: [{ kind: 'income_change', start: 1, pct: -20 }] },
  { id: 'emergency_2k', label: '$2,000 emergency', events: [{ kind: 'expense', start: 1, amount: 2000 }] },
]

function customSpec(c: Custom): ScenarioSpec | null {
  const ev: ScenarioEvent[] = []
  if (c.jobMonths > 0) ev.push({ kind: 'job_loss', start: 1, months: c.jobMonths })
  if (c.incomePct !== 0) ev.push({ kind: 'income_change', start: 1 + c.jobMonths, pct: c.incomePct })
  if (c.expense > 0) ev.push({ kind: 'expense', start: c.expenseMonth, amount: c.expense })
  if (!ev.length) return null
  const parts = [c.jobMonths ? `no income ${c.jobMonths} mo` : '', c.incomePct ? `income ${c.incomePct > 0 ? '+' : ''}${c.incomePct}%` : '', c.expense ? `${usd(c.expense)} cost` : '']
  return { id: 'custom', label: `Your what-if: ${parts.filter(Boolean).join(', ')}`, events: ev }
}

const TAG: Record<Verdict, string> = { holds: 'tag good', bends: 'tag warn', breaks: 'tag red' }
const short = (v: number) => (Math.abs(v) >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${Math.round(v)}`)

function Knob({ label, value, shown, min, max, step, onChange }: { label: string; value: number; shown: string; min: number; max: number; step: number; onChange: (v: number) => void }) {
  return (
    <div className="sl">
      <div className="top"><span>{label}</span><span className="val">{shown}</span></div>
      <div className="track"><input type="range" aria-label={label} min={min} max={max} step={step} value={value} onChange={(e) => onChange(+e.target.value)} /></div>
    </div>
  )
}

/** "What if" view on the Plan step: run the plan as edited through shocks, see if it holds, and how to adjust. */
export default function Scenarios({ cid, plan }: { cid: string; plan: PlanV2 }) {
  const [custom, setCustom] = useState<Custom>(NONE)
  const [run, setRun] = useState<ScenarioRun | null>(null)
  const [sel, setSel] = useState('job_loss_3m')
  const [range, setRange] = useState<ScenarioRange | null>(null)
  const [rangeBusy, setRangeBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const seq = useRef(0)
  const planKey = JSON.stringify(plan)
  useEffect(() => {
    const n = ++seq.current
    const cs = customSpec(custom)
    const t = setTimeout(() => {
      api<ScenarioRun>(`/customers/${cid}/scenarios`, { method: 'POST', body: JSON.stringify({ plan, scenarios: cs ? [...PRESETS, cs] : PRESETS }) })
        .then((r) => { if (n === seq.current) { setRun(r); setErr(null) } })
        .catch((e) => n === seq.current && setErr(e.message))
    }, 200)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cid, planKey, custom])

  useEffect(() => setRange(null), [cid, planKey])

  function setC(c: Custom) {
    setCustom(c)
    setSel(customSpec(c) ? 'custom' : sel === 'custom' ? 'job_loss_3m' : sel)
  }

  async function loadRange() {
    setRangeBusy(true)
    try { setRange(await api<ScenarioRange>(`/customers/${cid}/scenarios/range`, { method: 'POST', body: JSON.stringify({ plan, runs: 500 }) })) }
    catch (e) { setErr((e as Error).message) }
    finally { setRangeBusy(false) }
  }

  if (!run) return <div className="card"><h2>What if</h2><p className="small muted">{err ?? 'Running scenarios…'}</p></div>
  const r = run.results.find((x) => x.id === sel) ?? run.results[0]
  const data = run.reference.months.map((m, i) => {
    const g = range?.months[i]
    return {
      m: m.month, plan: m.net_worth, base: run.baseline.months[i]?.net_worth, hit: r?.sim.months[i]?.net_worth, adj: r?.adjusted.months[i]?.net_worth,
      lo: g?.p10, band: g ? g.p90 - g.p10 : undefined, mid: g?.p50,
    }
  })
  const names: Record<string, string> = { plan: 'Plan, nothing goes wrong', hit: 'Plan, hit by this', adj: 'Adjusted plan', base: 'Do nothing', mid: 'Likely (median)' }

  return (
    <>
      <div className="card">
        <div className="col-head"><h2 style={{ margin: 0 }}>What if</h2><span className="small muted">income {usd(run.monthly_income)}/mo · buffer {usd(run.emergency_buffer)}</span></div>
        <p className="why1">How your plan, as edited, holds up when life happens. Nothing here changes the plan.</p>
        <div className="scn-grid">
          {run.results.map((x) => (
            <button key={x.id} className={`scn ${x.id === r?.id ? 'on' : ''}`} onClick={() => setSel(x.id)}>
              <span className="lbl">{x.label}</span>
              <span><span className={TAG[x.verdict]}>{x.verdict}</span> <span className="sub">{x.why}</span></span>
              <span className="sub">net worth {x.nw_delta >= 0 ? '+' : ''}{usd(x.nw_delta)} in 2 yrs</span>
            </button>
          ))}
        </div>
      </div>

      {r && (
        <div className="card">
          <div className="col-head">
            <div className="legend">
              <span><i style={{ borderColor: ACCENT }} />Plan</span>
              <span><i className="dash" style={{ borderColor: BAD }} />Hit by this</span>
              <span><i style={{ borderColor: GOOD }} />Adjusted</span>
              <span><i className="dash" style={{ borderColor: GREY }} />Do nothing</span>
              {range && <span><i style={{ borderColor: '#c7d2fe' }} />Likely range (p10–p90)</span>}
            </div>
            <button className="ghost" onClick={loadRange} disabled={rangeBusy}>{rangeBusy ? 'Running 500 lives…' : range ? 'Re-run range' : 'Show likely range'}</button>
          </div>
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={data} margin={{ top: 12, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#e5e7eb" vertical={false} />
              <XAxis dataKey="m" tickFormatter={ymShort} interval={5} tick={{ fontSize: 11, fill: '#6b7280' }} tickLine={false} axisLine={{ stroke: '#e5e7eb' }} />
              <YAxis tickFormatter={short} tick={{ fontSize: 11, fill: '#6b7280' }} width={48} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
              <Tooltip formatter={(v, n) => (n === 'lo' || n === 'band' ? [null, null] : [usd(Number(v)), names[String(n)] ?? n])} labelFormatter={(m) => ym(String(m))} />
              {range && <Area dataKey="lo" stackId="r" stroke="none" fill="transparent" isAnimationActive={false} />}
              {range && <Area dataKey="band" stackId="r" stroke="none" fill="#c7d2fe" fillOpacity={0.5} isAnimationActive={false} />}
              <Line dataKey="base" stroke={GREY} strokeDasharray="5 4" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line dataKey="plan" stroke={ACCENT} strokeWidth={3} dot={false} isAnimationActive={false} />
              <Line dataKey="hit" stroke={BAD} strokeDasharray="6 4" strokeWidth={2} dot={false} isAnimationActive={false} />
              <Line dataKey="adj" stroke={GOOD} strokeWidth={2} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
          {range && <div className="small muted">500 simulated lives (each month: on track, drifting or a shock). High-cost debt $0 within 2 years in {Math.round(range.p_high_cost_free * 100)}% · card borrowing needed in {Math.round(range.p_borrowed * 100)}%.</div>}

          <div className="ba">
            <div><div className="k">High-cost $0</div><div className="from">{ym(run.reference.high_cost_debt_free)}</div><div className="to">{ym(r.sim.high_cost_debt_free)}</div></div>
            <div><div className="k">Lowest savings</div><div className="from">{usd(run.reference.min_cash)}</div><div className={`to ${r.sim.min_cash < run.emergency_buffer ? 'bad' : ''}`}>{usd(r.sim.min_cash)} · {ym(r.sim.min_cash_month)}</div></div>
            <div><div className="k">Put on the card</div><div className="from">{usd(0)}</div><div className={`to ${r.sim.borrowed_on_card > 0.5 ? 'bad' : 'good'}`}>{usd(r.sim.borrowed_on_card)}</div></div>
          </div>
          <div className="advice"><b>Adjusted plan · <span className={TAG[r.adjusted.verdict]}>{r.adjusted.verdict}</span></b> {r.adjusted.advice}</div>
        </div>
      )}

      <div className="card">
        <div className="col-head"><h3 style={{ margin: 0 }}>Your own what-if</h3>{customSpec(custom) && <button className="ghost" onClick={() => setC(NONE)}>Clear</button>}</div>
        <Knob label="Months with no income" value={custom.jobMonths} shown={custom.jobMonths ? `${custom.jobMonths} mo` : 'none'} min={0} max={6} step={1} onChange={(v) => setC({ ...custom, jobMonths: v })} />
        <Knob label="Income change after that" value={custom.incomePct} shown={`${custom.incomePct > 0 ? '+' : ''}${custom.incomePct}%`} min={-50} max={20} step={5} onChange={(v) => setC({ ...custom, incomePct: v })} />
        <Knob label="One-time expense" value={custom.expense} shown={usd(custom.expense)} min={0} max={10000} step={250} onChange={(v) => setC({ ...custom, expense: v })} />
        {custom.expense > 0 && (
          <Knob label="Lands in" value={custom.expenseMonth} shown={ym(run.reference.months[custom.expenseMonth]?.month)} min={1} max={run.reference.months.length - 1} step={1} onChange={(v) => setC({ ...custom, expenseMonth: v })} />
        )}
      </div>
      {err && <div className="error">{err}</div>}
    </>
  )
}
