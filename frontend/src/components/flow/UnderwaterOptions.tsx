import { useEffect, useRef, useState } from 'react'

import { api, usd } from '../../lib/api'
import type { OptionCard, OptionsCheck } from '../../lib/api'
import { DEBT_COLORS } from '../../lib/chart'

/** Underwater: tick options, watch the gap bar fill, then plan with them. */
type NoHelp = { key: string; title: string; why: string }

const money = (n: number) => usd(Math.round(n))
const SHORT = 'color-mix(in srgb, var(--bad) 40%, transparent)'
const lower = (t: string) => t.charAt(0).toLowerCase() + t.slice(1)
const brief = (s?: string) => (s ?? '').split(/(?<=[a-z])[.;]\s/)[0].replace(/\.$/, '')

export default function UnderwaterOptions({ cid, gap, cards: given, noHelp: givenNo, onApplied }: {
  cid: string; gap: number; cards?: OptionCard[]; noHelp?: NoHelp[]; onApplied: () => void
}) {
  const [cards, setCards] = useState<OptionCard[]>(given ?? [])
  const [noHelp, setNoHelp] = useState<NoHelp[]>(givenNo ?? [])
  const [picked, setPicked] = useState<string[]>([])
  const [res, setRes] = useState<OptionsCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const seq = useRef(0)

  useEffect(() => {
    const init = (cs: OptionCard[]) => {
      setCards(cs)
      const rec = cs.filter((c) => c.modelled && c.recommended && !c.never_recommend && (c.closes_per_month ?? 0) > 0.5)
      setPicked((rec.length ? rec : cs.filter((c) => c.modelled && !c.never_recommend && (c.closes_per_month ?? 0) > 0.5)).map((c) => c.key))
    }
    if (given?.length) { init(given); return }
    api<{ options: OptionCard[]; not_helping: NoHelp[] }>(`/customers/${cid}/options`)
      .then((o) => { init(o.options); setNoHelp(o.not_helping ?? []) }).catch((e) => setErr(String(e)))
  }, [cid, given])

  // Smallest first, so the rows and the bar read in the same order.
  const helps = cards.filter((c) => c.modelled && (c.closes_per_month ?? 0) > 0.5).sort((a, b) => (a.closes_per_month ?? 0) - (b.closes_per_month ?? 0))
  const noEffect = [...cards.filter((c) => c.modelled && !((c.closes_per_month ?? 0) > 0.5)).map((c) => c.title), ...noHelp.map((n) => n.title)]
  const ask = cards.filter((c) => !c.modelled)
  // Smallest first, so the bar reads as a waterfall and the last pick takes what's left.
  const order = helps.filter((c) => picked.includes(c.key)).sort((a, b) => (a.closes_per_month ?? 0) - (b.closes_per_month ?? 0)).map((c) => c.key)

  useEffect(() => {
    const n = ++seq.current
    if (!order.length) { setRes(null); return }
    const t = setTimeout(() => {
      api<OptionsCheck>(`/customers/${cid}/options/check`, { method: 'POST', body: JSON.stringify({ keys: order }) })
        .then((r) => n === seq.current && setRes(r)).catch((e) => n === seq.current && setErr(String(e)))
    }, 150)
    return () => clearTimeout(t)
  }, [cid, order.join(',')])

  if (!cards.length) return null
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))
  const total = res?.gap_before ?? gap
  const steps = (res?.steps ?? []).filter((s) => s.closes > 0.5)
  const left = res ? res.gap_after : total
  // A picked row shows what it adds on top of the ones before it (the bar's share), not its standalone amount.
  const share = (c: OptionCard) => steps.find((s) => s.key === c.key)?.closes ?? c.closes_per_month ?? 0
  const color = (k: string) => DEBT_COLORS[helps.findIndex((c) => c.key === k) % DEBT_COLORS.length]

  const apply = async () => {
    setBusy(true); setErr('')
    try {
      await api(`/customers/${cid}/options/apply`, { method: 'POST', body: JSON.stringify({ keys: order }) })
      onApplied()
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <div className="col-head">
        <h2 style={{ margin: 0 }}>Close the {money(total)}/mo gap</h2>
        <b className={left > 0.5 ? 'bad' : 'good'} style={{ fontVariantNumeric: 'tabular-nums' }}>{left > 0.5 ? `${money(left)}/mo short` : 'Closed'}</b>
      </div>
      <div className="pd-bar" role="img" aria-label={`${money(total - left)} of ${money(total)} closed`}>
        {steps.map((s) => <div key={s.key} style={{ width: `${(Math.min(s.closes, total) / total) * 100}%`, background: color(s.key) }} />)}
        {left > 0.5 && <div style={{ width: `${(left / total) * 100}%`, background: SHORT }} />}
      </div>
      <div className="legend">
        {steps.map((s) => <span key={s.key}><i className="sw" style={{ background: color(s.key) }} />{s.title} −{money(s.closes)}</span>)}
        {left > 0.5 && <span><i className="sw" style={{ background: SHORT }} />Still short {money(left)}</span>}
      </div>

      <div style={{ marginTop: 12 }}>
        {helps.map((c) => (
          <label key={c.key} style={{ display: 'grid', gridTemplateColumns: '18px 1fr auto', gap: '0 10px', padding: '8px 0', borderTop: '1px solid var(--line)', alignItems: 'baseline' }}>
            <input type="checkbox" checked={picked.includes(c.key)} onChange={() => toggle(c.key)} />
            <b>{c.title}</b>
            <b className="good" style={{ fontVariantNumeric: 'tabular-nums' }}>−{money(share(c))}/mo</b>
            <span />
            <span className="small muted" style={{ gridColumn: '2 / 4' }}>{brief(c.why || c.detail)}{c.new_payment != null && <> · new payment ≈ {money(c.new_payment)}/mo</>}</span>
          </label>
        ))}
      </div>
      {!!ask.length && <p className="small muted" style={{ margin: '8px 0 0' }}>Also worth asking, not counted: {ask.map((c) => lower(c.title)).join(', ')}.</p>}
      {!!noEffect.length && <p className="small muted" style={{ margin: '4px 0 0' }}>Doesn't help here: {noEffect.map(lower).join(', ')}.</p>}

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12 }}>
        <button className="primary" disabled={busy || !res?.closes} onClick={apply}>{busy ? 'Saving…' : 'Plan with these'}</button>
        {res && !res.closes && <span className="small muted">Tick more to close the gap.</span>}
        {err && <span className="small" style={{ color: 'var(--bad-ink)' }}>{err}</span>}
      </div>
    </div>
  )
}
