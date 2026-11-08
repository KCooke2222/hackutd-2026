# Agent v3: code does the math, the agent makes the calls

Goal: first plan in about a minute (now 3 to 5 min), and the agent spends its time on decisions that
need judgement, not on re-reading static data.

## Why change

A recorded Fixable run (197 s):

| Step | Time | What it does |
|---|---|---|
| gather | 1 s | code |
| diagnose | 34 s | 1 LLM call, no tools |
| plan | 108 s | 5 LLM turns: debt_priority, liquidity_ladder, cash_flow, find_recurring, spending_detail x4, compare_sources x2, then hand-guessed simulate_plan budgets |
| explain | 53 s | 1 LLM call |

Every data tool returns the same thing every run. `propose_plan` replaces the model's numbers with the
solver's anyway, so the budget guessing is wasted.

## Roles

- **Code (deterministic, instant):** data, the solver, trade-offs, suggested cuts, the reality check,
  simulation and validation. "The AI never does the math."
- **Agent (judgement):** what to suggest, what's odd, what to do when there is no good plan, working
  out the plan with the customer, the monthly check-in, and proposing actions through tools.

The reality check stays a deterministic test: "can the debt shrink at all, even with every cut at its
floor?" That is a math question. When the answer is no, the **agent** works out the plan of action.

## The new first run

1. **Brief (code, ~2 s).** One JSON brief: balances, cash flow (pays vs net paydown), debt order,
   cushion, recurring merchants, flags, the solver plan with suggested cuts, trade-offs per cut, and
   the reality check. The base plan shows on screen right away.
2. **Agent pass (1 to 2 LLM calls, reasoning on).** Input is the brief. Output is JSON:
   - which suggested cuts to keep, plus buffer months, each with a one-line why;
   - a short read of what's driving the debt (replaces diagnose);
   - when the reality check fails: a worked-out set of options for Underwater, not a verdict.
     Each option is a card: what it is, what it closes of the monthly gap (solver-checked where it
     can be modelled: deeper cuts, pausing contributions, selling assets, a smaller cushion; stated
     plainly where it can't: hardship programs, consolidation, asking for a lower rate, extra
     income), and its downside. The screen asks "Which of these would you do?": the customer
     ticks the ones that seem reasonable, or talks it through in chat. The agent re-solves with
     the picked options and shows whether the gap closes, then proposes the plan. This is the
     part that actually figures things out.
   - **A little real tool use, bounded:** if the brief has flags (spending spike, unknown outflow,
     new subscription), the agent may make at most 2 tool calls (`spending_detail`, `sql`) to look
     into them before answering. No flags means no tool calls.
3. **Propose (tool call).** The agent calls `propose_plan` with its soft inputs. The solver
   re-solves and validates, and that creates the pending proposal.
4. **Explain** is part of step 2's output, not a separate call.

Thinking screen: Reading accounts, Building the plan (code), Checking odd spending (only when
flagged), Choosing what to suggest, Plan ready.

## Where the agent stays agentic

1. **Chat editing (exists, being improved).** "Don't cut travel", "keep 6 months", "I can cut dining
   more": the agent maps each to solver inputs (declined cuts, targets, buffer, rules), re-solves,
   compares with the current plan and explains the trade-off. It keeps or rejects the change.
2. **Actions through tools (exists).** `propose_plan`, then the customer approves, then
   `execute.schedule` makes the real Nessie transfer (cash to the PNC card) and records the
   scheduled steps. The agent never acts without approval.
3. **Monthly check-in (new, small).** On "advance a month" (tracking scenarios: overspend, income
   drop, windfall, missed minimum), the agent reads the tracking status and the fixes code already
   computes. It names the drift ("dining ran $210 over, debt-free slips 2 months"), picks a fix and
   says why, and proposes it as a pending proposal. Once approved, it executes. This is the
   "acts on its own, not just a chatbot" moment in the demo.

## Speed settings

- Keep reasoning on everywhere. The speed comes from fewer calls, not from thinking less.
- Keep `nvidia/nemotron-3.5-super-vl-120b-a12b` (fastest tested).
- Stream the agent pass's text so the screen fills while it writes.

## Out of scope for now

- A full agent loop over all data tools (gone from the first run; the tools stay for chat).
- Separate diagnose and explain calls.

## Order of work

1. Land suggested cuts and net paydown (in progress).
2. The brief plus the single agent pass, with bounded tool calls on flags; the new Thinking steps.
3. Underwater action plan in the agent pass.
4. Agent in the monthly check-in.
5. Re-record replays for Fixable and Underwater.
6. Plan page redesign from the mockup.

Done when: Fixable's first plan is under about 60 s with suggested cuts and a why per action; Underwater
gets option cards to pick from, and picking re-solves and shows whether the gap closes; chat edits re-solve; one check-in month shows the agent spotting drift
and proposing a fix; pytest is green.
