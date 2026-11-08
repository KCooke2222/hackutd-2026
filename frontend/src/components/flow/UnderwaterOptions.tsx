import { useEffect, useState } from 'react'

import { api, usd } from '../../lib/api'
import type { OptionCard, OptionsCheck } from '../../lib/api'

/** "Which of these would you do?": tick options, re-check whether the monthly gap closes. */
export default function UnderwaterOptions({ cid, cards: given }: { cid: string; cards?: OptionCard[] }) {
  const [cards, setCards] = useState<OptionCard[]>(given ?? [])
  const [picked, setPicked] = useState<string[]>(() => (given ?? []).filter((c) => c.recommended).map((c) => c.key))
  const [income, setIncome] = useState<number | ''>('')
  const [res, setRes] = useState<OptionsCheck | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (given?.length) return
    api<{ options: OptionCard[] }>(`/customers/${cid}/options`).then((o) => setCards(o.options)).catch((e) => setErr(String(e)))
  }, [cid, given])
  useEffect(() => {
    const inc = cards.find((c) => c.key === 'extra_income')
    if (inc?.amount && income === '') setIncome(inc.amount)
  }, [cards, income])

  if (!cards.length) return null
  const toggle = (k: string) => setPicked((p) => (p.includes(k) ? p.filter((x) => x !== k) : [...p, k]))
  const check = async () => {
    setBusy(true); setErr('')
    try {
      setRes(await api<OptionsCheck>(`/customers/${cid}/options/check`, {
        method: 'POST', body: JSON.stringify({ keys: picked, extra_income: income === '' ? null : income }),
      }))
    } catch (e) { setErr(String(e)) } finally { setBusy(false) }
  }

  return (
    <div className="card">
      <h2 style={{ margin: '0 0 4px' }}>Which of these would you do?</h2>
      <p className="small muted" style={{ margin: '0 0 10px' }}>Tick the ones that seem reasonable, then check whether the gap closes.</p>
      {cards.map((c) => (
        <label key={c.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '8px 0', borderTop: '1px solid var(--line, #8883)' }}>
          <input type="checkbox" checked={picked.includes(c.key)} onChange={() => toggle(c.key)} style={{ marginTop: 4 }} />
          <div style={{ flex: 1 }}>
            <div><b>{c.title}</b>{c.recommended && <span className="small muted"> · suggested</span>}</div>
            <div className="small">
              {c.modelled
                ? c.closes_per_month && c.closes_per_month > 0.5
                  ? <>Closes {usd(c.closes_per_month)}/mo of the gap{c.closes_gap ? ' (all of it)' : ''}.</>
                  : <>Closes none of the gap on its own{c.cash_runs_out ? `; cash still runs out ${c.cash_runs_out}` : ''}.</>
                : <>Can't be modelled: depends on a lender or a decision.</>}
              {c.new_payment != null && <> New payment about {usd(c.new_payment)}/mo.</>}
              {' '}{c.why || c.detail}
            </div>
            {c.downside && <div className="small muted">Downside: {c.downside}</div>}
            {c.key === 'extra_income' && picked.includes(c.key) && (
              <div className="small" style={{ marginTop: 4 }}>
                $/mo <input type="number" value={income} min={0} step={50} style={{ width: 90 }}
                  onChange={(e) => setIncome(e.target.value === '' ? '' : Number(e.target.value))} />
              </div>
            )}
          </div>
        </label>
      ))}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
        <button className="primary" disabled={busy || !picked.length} onClick={check}>{busy ? 'Checking…' : 'Does it close the gap?'}</button>
        {err && <span className="small" style={{ color: 'var(--bad-ink)' }}>{err}</span>}
      </div>
      {res && (
        <div className="notice small" style={{ marginTop: 10 }}>
          <b>{res.closes ? 'Yes: the debt can shrink.' : `Still ${usd(res.gap_after)}/mo short (was ${usd(res.gap_before)}).`}</b> {res.message}
          {!!res.not_modelled.length && <> Not counted: {res.not_modelled.length} option(s) the lender decides.</>}
        </div>
      )}
    </div>
  )
}
