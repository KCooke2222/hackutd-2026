import { useState } from 'react'

import type { Step } from '../../lib/steps'

export type { Step }

const ICON: Record<string, string> = {
  phase_gather: '≡', phase_diagnose: '?', phase_explain: '✎', phase_interpret: '“', phase_keep: '✓',
  sql: '⌕', cash_flow: '⇄', spending_detail: '▦', find_recurring: '↻', compare_sources: '⚖', debt_priority: '#',
  liquidity_ladder: '▤', simulate_plan: '∿', propose_plan: '✓',
}

/** Agent steps as chips: tool, one number, reasoning collapsed. Shared by the first run and talk-to-the-plan. */
export default function Steps({ steps, done, waiting = 'Reading your accounts' }: { steps: Step[]; done: boolean; waiting?: string }) {
  const [open, setOpen] = useState<Record<string, boolean>>({})
  return (
    <div style={{ marginTop: 6 }}>
      {steps.map((s) => {
        const running = s.result == null && !done
        return (
          <div key={s.id} className={`step ${running ? 'run' : ''}`}>
            <span className="ic">{running ? <span className="spin" /> : ICON[s.name] ?? '•'}</span>
            <span>{s.label}{s.why && <button className="tog" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>{open[s.id] ? 'hide' : 'why'}</button>}</span>
            <span className="res">{s.result ?? '…'}</span>
            {open[s.id] && <span className="why">{s.why}</span>}
          </div>
        )
      })}
      {!steps.length && !done && <div className="step run"><span className="ic"><span className="spin" /></span><span className="muted">{waiting}</span><span /></div>}
    </div>
  )
}
