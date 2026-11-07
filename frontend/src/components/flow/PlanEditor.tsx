import { usd } from '../../lib/api'
import type { Proposal } from '../../lib/api'
import { TIER_NAME, knobsFrom, payoffLabel } from '../../lib/flow'
import type { Knobs, Outlook } from '../../lib/flow'

type Slider = { key: 'lump' | 'pay' | 'cap'; label: string; min: number; max: number; step: number; fmt: (v: number) => string; meta: string }

function SliderRow({ s, value, ai, onChange }: { s: Slider; value: number; ai: number; onChange: (v: number) => void }) {
  const p = s.max > s.min ? Math.min(1, Math.max(0, (ai - s.min) / (s.max - s.min))) : 0
  return (
    <div className="sl">
      <div className="top"><span>{s.label}</span><span className="val">{s.fmt(value)}</span></div>
      <div className="track">
        <input type="range" aria-label={s.label} min={s.min} max={s.max} step={s.step} value={value} onChange={(e) => onChange(+e.target.value)} />
        <span className="tick" style={{ left: `calc(8px + (100% - 16px) * ${p})` }} title={`AI: ${s.fmt(ai)}`} />
      </div>
      <div className="meta">{s.meta}</div>
    </div>
  )
}

const roundUp = (n: number, to: number) => Math.ceil(n / to) * to

export default function PlanEditor({ ai, why, knobs, setKnobs, outlook, busy, error, onApprove, onReject }: {
  ai: Proposal; why: string; knobs: Knobs; setKnobs: (k: Knobs) => void; outlook: Outlook | null
  busy: boolean; error: string | null; onApprove: () => void; onReject: () => void
}) {
  const aiK = knobsFrom(ai)
  const o = outlook
  const cur = o?.current ?? { monthly_payment: ai.simulation.inputs.current_monthly_payment, monthly_spending: ai.simulation.inputs.current_monthly_spending }
  const lim = o?.limits
  const lumpMax = lim ? Math.min(lim.card_balance, lim.cash + lim.cd + lim.brokerage) : aiK.lump
  const sources = o?.draws.length ? o.draws.map((d) => TIER_NAME[d.key] ?? d.key).join(' + ') : null
  const tap = o?.tap_cost_total ?? 0
  const floor = lim?.spend_floor ?? 0
  const minPay = lim?.min_payment ?? 0
  const pay = knobs.pay || cur.monthly_payment
  const disc = (o?.categories ?? []).filter((c) => !c.essential)
  const discTotal = disc.reduce((a, c) => a + c.monthly_avg, 0)
  const keepShare = discTotal > 0 ? Math.max(0, Math.min(1, (knobs.cap - floor) / discTotal)) : 1

  const sliders: Slider[] = [
    {
      key: 'lump', label: 'Lump sum → card', min: 0, max: Math.max(lumpMax, aiK.lump), step: 50, fmt: (v) => usd(v),
      meta: knobs.lump > 0 ? `from ${sources ?? '…'} · ${tap ? `one-time cost ${usd(tap)}` : 'no cost'}` : 'none',
    },
    { key: 'cap', label: 'Card spending cap', min: Math.ceil(floor), max: Math.ceil(Math.max(cur.monthly_spending, aiK.cap)), step: 10, fmt: (v) => `${usd(v)}/mo`, meta: `essentials ${usd(floor)} · now ${usd(cur.monthly_spending)}` },
  ]
  const paySlider: Slider = { key: 'pay', label: 'Monthly payment', min: Math.ceil(minPay), max: roundUp(Math.max(3000, cur.monthly_payment * 2, aiK.pay), 100), step: 10, fmt: (v) => `${usd(v)}/mo`, meta: `minimum ${usd(minPay)} · now ${usd(cur.monthly_payment)}` }

  const s = o?.scenarios
  const saved = o?.summary.interest_saved_12m ?? 0
  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Plan</h2>
        <button className="ghost" onClick={() => setKnobs(aiK)}>Reset to AI</button>
      </div>
      {why && <p className="why1">{why}</p>}
      {sliders.map((sl) => <SliderRow key={sl.key} s={sl} value={knobs[sl.key]} ai={aiK[sl.key]} onChange={(v) => setKnobs({ ...knobs, [sl.key]: v })} />)}
      <label className="sl autopay">
        <span className="top"><span><input type="checkbox" checked={knobs.autopay} onChange={(e) => setKnobs({ ...knobs, autopay: e.target.checked })} /> Autopay full statement</span>
          {o && o.summary.freed_monthly > 0 && <span className="val good">freed {usd(o.summary.freed_monthly)}/mo</span>}</span>
        <span className="meta">{knobs.autopay ? `${usd(pay)}/mo until $0, then the statement · freed money → savings` : 'Off: pick a payment'}</span>
      </label>
      {!knobs.autopay && <SliderRow s={paySlider} value={pay} ai={aiK.pay || cur.monthly_payment} onChange={(v) => setKnobs({ ...knobs, pay: v })} />}
      <details className="more">
        <summary>More</summary>
        {o && o.categories.map((c) => (
          <div key={c.category} className="row">
            <span>{c.essential ? '🔒 ' : ''}{c.category[0].toUpperCase() + c.category.slice(1)}</span>
            <span className="num">{c.essential ? usd(c.monthly_avg) : <>{usd(c.monthly_avg * keepShare)} <span className="small muted">−{Math.round((1 - keepShare) * 100)}%</span></>}</span>
          </div>
        ))}
        <div className="row">
          <span>Keep in cash</span>
          <span className="num"><input type="number" min={3} max={12} step={0.5} value={knobs.keep} onChange={(e) => setKnobs({ ...knobs, keep: Math.max(3, +e.target.value || 3) })} style={{ width: 70 }} /> months{lim && <span className="small muted"> · {usd(lim.emergency_buffer)}</span>}</span>
        </div>
        {lim && <div className="small muted">Available: savings {usd(lim.cash)} · CD {usd(lim.cd)} · brokerage {usd(lim.brokerage)}</div>}
      </details>

      {s && o && (
        <div className="ba">
          <div><div className="k">Card $0</div><div className="from">{payoffLabel(s.today.payoff_month)}</div><div className={`to ${s.plan.payoff_month != null ? 'good' : 'bad'}`}>{payoffLabel(s.plan.payoff_month)}</div></div>
          <div><div className="k">Interest / yr</div><div className="from">{usd(s.today.interest_first_12m)}</div><div className="to">{usd(s.plan.interest_first_12m)} {saved > 0 && <span className="small good">−{usd(saved)}</span>}</div></div>
          <div><div className="k">One-time cost</div><div className="from">{usd(0)}</div><div className="to">{usd(tap)}</div></div>
        </div>
      )}
      {(error || (o && !o.valid)) && <div className="error">{error ?? o?.error}</div>}
      {o?.summary.relapse_month != null && <div className="notice small">Spending is above the payment: the card refills by {payoffLabel(o.summary.relapse_month)}.</div>}
      <div className="actions">
        <button className="primary big" onClick={onApprove} disabled={busy || !o?.valid || s?.plan.payoff_month == null}>{busy ? 'Approving…' : 'Approve'}</button>
        <button className="big" onClick={onReject} disabled={busy}>Reject</button>
      </div>
    </div>
  )
}
