import { useState } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  ArrowLeftRight, ChartColumn, ChartSpline, CircleCheck, Database, Dot, Layers, ListOrdered, LoaderCircle,
  MessageSquareText, PenLine, Repeat, Scale, Search, Stethoscope,
} from 'lucide-react'

import type { Step } from '../../lib/steps'

export type { Step }

const ICON: Record<string, LucideIcon> = {
  phase_gather: Database, phase_diagnose: Stethoscope, phase_explain: PenLine, phase_interpret: MessageSquareText, phase_keep: CircleCheck,
  sql: Search, cash_flow: ArrowLeftRight, spending_detail: ChartColumn, find_recurring: Repeat, compare_sources: Scale, debt_priority: ListOrdered,
  liquidity_ladder: Layers, simulate_plan: ChartSpline, propose_plan: CircleCheck,
}

function StepIcon({ name, running }: { name: string; running: boolean }) {
  if (running) return <LoaderCircle size={15} strokeWidth={2} className="spin-ic" />
  const I = ICON[name] ?? Dot
  return <I size={15} strokeWidth={2} />
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
            <span className="ic"><StepIcon name={s.name} running={running} /></span>
            <span>{s.label}{s.why && <button className="tog" onClick={() => setOpen({ ...open, [s.id]: !open[s.id] })}>{open[s.id] ? 'hide' : 'why'}</button>}</span>
            <span className="res">{s.result ?? '…'}</span>
            {open[s.id] && <span className="why">{s.why}</span>}
          </div>
        )
      })}
      {!steps.length && !done && <div className="step run"><span className="ic"><StepIcon name="" running /></span><span className="muted">{waiting}</span><span /></div>}
    </div>
  )
}
