export type Step = { id: string; name: string; label: string; why: string; result?: string }

/** Wire an EventSource's `call` / `result` events into a step list. */
export function listenSteps(es: EventSource, update: (f: (s: Step[]) => Step[]) => void) {
  es.addEventListener('call', (e) => {
    const d = JSON.parse((e as MessageEvent).data)
    update((s) => [...s, { id: d.id, name: d.name, label: d.label, why: d.why }])
  })
  es.addEventListener('result', (e) => {
    const d = JSON.parse((e as MessageEvent).data)
    update((s) => s.map((x) => (x.id === d.id ? { ...x, result: d.result } : x)))
  })
}
