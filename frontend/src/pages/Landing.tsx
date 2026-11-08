import { Bot, Calculator, Check, ChevronRight, LineChart, Landmark, ListChecks, Search } from 'lucide-react'
import Brand from '../components/Brand'

const STEPS = [
  { icon: Search, name: 'Snapshot', text: 'Reads your accounts and spending.' },
  { icon: Bot, name: 'Thinking', text: 'Finds why the balance won’t go down.' },
  { icon: ListChecks, name: 'Plan', text: 'One plan to $0. Adjust it in plain words.' },
  { icon: Check, name: 'Done', text: 'You approve; your payments are set up.' },
]

const BUILT = [
  { icon: Landmark, text: 'Real bank data via Nessie' },
  { icon: Calculator, text: 'A solver does every number' },
  { icon: Bot, text: 'AI makes the judgement calls' },
]

/** Welcome page at `/`. `start(cid)` enters the 4-step flow for a demo customer. */
export default function Landing({ start }: { start: (cid: string) => void }) {
  return (
    <div className="app">
      <header className="masthead">
        <div className="masthead-in">
          <Brand />
          <span className="who"><button className="primary" onClick={() => start('C001')}>Get started</button></span>
        </div>
      </header>
      <main className="page landing">
        <section className="land-hero">
          <h1>Get your debt under control.</h1>
          <p className="muted">Paidway builds one plan to pay off your debt as fast as your money allows, lets you adjust it to your life, then coaches you through it.</p>
          <div className="land-cta">
            <button className="primary big" onClick={() => start('C001')}>Get started <ChevronRight size={16} /></button>
          </div>
        </section>

        <div className="card">
          <h2>Four steps to a plan</h2>
          <ol className="land-steps">
            {STEPS.map((s, i) => (
              <li key={s.name}>
                <span className="n">{i + 1}</span>
                <s.icon size={18} className="ic" />
                <b>{s.name}</b>
                <span className="muted small">{s.text}</span>
              </li>
            ))}
          </ol>
          <p className="muted small" style={{ margin: '10px 0 0' }}>Nothing moves until you approve.</p>
        </div>

        <div className="card land-coach">
          <LineChart size={22} className="ic" />
          <div>
            <h2>Then we coach you</h2>
            <p className="muted" style={{ margin: 0 }}>Monthly check-ins against your plan: what's on track, what slipped, and a re-plan when life changes.</p>
          </div>
        </div>

        <div className="card">
          <h2>Try a demo</h2>
          <div className="land-demos">
            <button onClick={() => start('C001')}><b>Fixable</b><span className="muted small">Card debt that keeps refilling. A plan clears it.</span></button>
            <button onClick={() => start('C027')}><b>Underwater</b><span className="muted small">Short every month. The agent finds the way out.</span></button>
          </div>
        </div>

        <ul className="land-built">
          {BUILT.map((b) => <li key={b.text}><b.icon size={15} className="ic" />{b.text}</li>)}
        </ul>
      </main>
    </div>
  )
}
