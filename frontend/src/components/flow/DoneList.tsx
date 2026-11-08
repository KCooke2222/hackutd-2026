import { useState } from 'react'
import type { ReactNode } from 'react'
import { Check, Clock, X } from 'lucide-react'
import { usd } from '../../lib/api'
import type { NessieCall } from '../../lib/api'
import NessieChip from '../NessieChip'
import { pretty, what } from './AgentActions'
import '../../styles/done.css'

type Quiet = { step: string; how?: string[] | string; link?: string }
type State = 'ok' | 'bad' | 'held'

const state = (c: NessieCall): State =>
  c.status === 'error' ? 'bad' : c.status === 'not_sent' || c.status === 'skipped' ? 'held' : 'ok'
const worst = (cs: NessieCall[]): State =>
  cs.some((c) => state(c) === 'bad') ? 'bad' : cs.some((c) => state(c) === 'held') ? 'held' : 'ok'

/** One line of the checklist: a payoff (transfer + payment, maybe a modelled sale cost) is one row, not two or three. */
type Row = { key: string; title: string; sub: string; amount: number; per?: string; calls: NessieCall[] }

function rows(calls: NessieCall[]): Row[] {
  const out: Row[] = []
  const byGroup = new Map<string, Row>()
  calls.forEach((c, i) => {
    const g = c.group && !c.tool.endsWith('create_bill') ? c.group : null
    const row = g ? byGroup.get(g) : undefined
    if (row) {
      row.calls.push(c)
      if (c.tool.endsWith('create_withdrawal')) row.sub += ` · ${usd(c.amount)} est. sale cost`
      return
    }
    let r: Row
    if (c.tool.endsWith('create_bill')) {
      const w = what(c)
      r = { key: `b${i}`, title: `Autopay: ${c.from ?? 'Checking'} pays ${c.to ?? 'the debt'}`, sub: `Recurring bill ${w.when.replace(/^\/mo · /, '')}`, amount: c.amount, per: '/mo', calls: [c] }
    } else if (c.tool.endsWith('create_transfer') || c.tool.endsWith('create_deposit')) {
      r = { key: g ?? `t${i}`, title: `Paid off ${c.to}`, sub: `from ${c.from} · today`, amount: c.amount, calls: [c] }
    } else {
      const w = what(c)
      r = { key: g ?? `w${i}`, title: w.text, sub: `${w.verb} · today`, amount: c.amount, calls: [c] }
    }
    if (g) byGroup.set(g, r)
    out.push(r)
  })
  return out
}

function Box({ s, onClick, label }: { s: State | 'todo' | 'ticked'; onClick?: () => void; label: string }) {
  const icon = s === 'ok' || s === 'ticked' ? <Check size={14} strokeWidth={3} /> : s === 'bad' ? <X size={14} strokeWidth={3} /> : s === 'held' ? <Clock size={12} strokeWidth={2.5} /> : null
  return onClick
    ? <button type="button" className={`cbox ${s}`} role="checkbox" aria-checked={s === 'ticked'} aria-label={label} onClick={onClick}>{icon}</button>
    : <span className={`cbox ${s}`} role="img" aria-label={label}>{icon}</span>
}

/** The Done screen's top card: "Done", then a checklist, a ticked box per move that ran in Nessie, an open box per step left. */
export default function DoneList({ calls, held = false, recorded = [], manual = [], note, onRetry, retrying = false, children }: {
  calls: NessieCall[]; held?: boolean; recorded?: Quiet[]; manual?: Quiet[]; note?: ReactNode
  onRetry?: () => void; retrying?: boolean; children?: ReactNode
}) {
  const list = rows(calls)
  const failed = list.filter((r) => worst(r.calls) === 'bad').length
  const [ticked, setTicked] = useState<Record<string, boolean>>({})
  const toggle = (k: string) => setTicked((t) => ({ ...t, [k]: !t[k] }))

  const head = held ? 'Approved' : failed ? 'Almost done' : 'Done'
  const lede = held
    ? 'Your plan is approved. These moves are recorded, not sent to Nessie yet.'
    : failed
      ? `${list.length - failed} of ${list.length} moves went through in Nessie. Retry the rest.`
      : list.length
        ? `Your plan is approved and ${list.length === 1 ? 'its move ran' : `all ${list.length} moves ran`} in Capital One Nessie.`
        : 'Your plan is approved. Nothing needed to run in Nessie.'

  return (
    <div className="card done-card">
      <div className="done-head">
        <span className={`done-badge ${held ? 'held' : failed ? 'bad' : 'ok'}`}>{held ? <Clock size={22} strokeWidth={2.5} /> : failed ? <X size={22} strokeWidth={3} /> : <Check size={24} strokeWidth={3} />}</span>
        <div style={{ minWidth: 0 }}>
          <h2>{head}</h2>
          <div className="lede">{lede}</div>
        </div>
      </div>
      {note && <div className="small muted" style={{ margin: '0 0 6px' }}>{note}</div>}

      {list.length > 0 && (
        <>
          <div className="done-sec"><span>In Nessie</span><NessieChip label="Capital One Nessie" title="Real Nessie API calls made on approve" /></div>
          <ul className="checklist">
            {list.map((r) => {
              const s = worst(r.calls)
              return (
                <li key={r.key} className={s}>
                  <Box s={s} label={s === 'ok' ? 'Done' : s === 'bad' ? "Didn't go through" : 'Not sent yet'} />
                  <div className="txt">
                    <div className="t">{r.title}</div>
                    <div className="s">
                      {r.sub}
                      {s === 'bad' && <> · <span className="bad">didn't go through</span></>}
                      {s === 'held' && <> · not sent yet</>}
                      {s === 'ok' && r.calls.some((c) => c.retried) && <> · went through on retry</>}
                    </div>
                  </div>
                  <div className="amt">{usd(r.amount)}{r.per && <small>{r.per}</small>}</div>
                  {s === 'bad' && onRetry && <button className="small retry" onClick={onRetry} disabled={retrying}>{retrying ? 'Retrying…' : 'Retry'}</button>}
                </li>
              )
            })}
          </ul>
        </>
      )}

      {manual.length > 0 && (
        <>
          <div className="done-sec"><span>Your to-do</span></div>
          <ul className="checklist todo">
            {manual.map((m, i) => {
              const k = `m${i}`
              return (
                <li key={k} className={ticked[k] ? 'ticked' : ''}>
                  <Box s={ticked[k] ? 'ticked' : 'todo'} onClick={() => toggle(k)} label={pretty(m.step)} />
                  <div className="txt">
                    <div className="t">{pretty(m.step)}</div>
                    {(m.how || m.link) && <div className="s">{m.how && (Array.isArray(m.how) ? m.how.join(' · ') : m.how)}{m.link && <>{m.how ? ' · ' : ''}<a href={m.link} target="_blank" rel="noreferrer">where</a></>}</div>}
                  </div>
                </li>
              )
            })}
          </ul>
        </>
      )}

      {recorded.length > 0 && (
        <>
          <div className="done-sec"><span>Each month</span><span className="hint">the monthly check-in ticks these</span></div>
          <ul className="checklist todo">
            {recorded.map((m, i) => {
              const k = `r${i}`
              return (
                <li key={k} className={ticked[k] ? 'ticked' : ''}>
                  <Box s={ticked[k] ? 'ticked' : 'todo'} onClick={() => toggle(k)} label={pretty(m.step)} />
                  <div className="txt"><div className="t">{pretty(m.step)}</div></div>
                </li>
              )
            })}
          </ul>
        </>
      )}
      {children}
    </div>
  )
}
