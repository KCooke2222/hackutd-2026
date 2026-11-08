# Recorded agent runs (replay fixtures)

Each `<customer>-<run_id>.json` is one live agent run: every SSE event with its time offset, plus the
proposal it produced. The API imports them into SQLite on startup (restoring the proposal if missing), and
the UI plays them through the Thinking screen: "Load cached run", or `/?replay=<run_id>&speed=8`.

These capture the data the agent saw when recorded. They go stale whenever the seed data or the agent
changes (e.g. after PR 8): re-record with

    make record CUSTOMER=C001 RUNS=2    # replaces that customer's fixtures; needs NVIDIA_API_KEY, ~3-5 min per run
