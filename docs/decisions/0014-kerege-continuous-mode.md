# 0014 — Kerege continuous mode: one graph run per developer round

Date: 2026-10-17 · Status: accepted

## Context

The owner wants the dev team (now called **Kerege**) to keep working for hours: when one
developer finishes, plan its next task while the other keeps going. The existing graph plans
every developer at once and ends when all parallel tasks end, so a fast developer idles while a
slow one runs its benchmark.

## Decision

- `devteam run --continuous` runs one loop (thread) per developer. Each loop runs ordinary
  rounds of the same graph, but with the input `developers: [<that one>]`: the brain plans only
  for it. Each round is its own checkpoint thread, so crash / stop / resume behave exactly as
  in a single `devteam run`; unfinished rounds are listed in `.state/continuous.json` and
  resumed first by the next continuous run (their `in_progress` claims are not treated as
  backlog drift).
- A clean stop is a flag checked at the start of every node (`StopRun`): the running step
  (a developer session, checks, a CI wait) finishes and is checkpointed, nothing new starts.
  First Ctrl+C, `max_hours`, budget, 2 blocked in a row and crashes use it; `max_tasks` only
  stops taking new tasks. Second Ctrl+C and `devteam stop` still kill at once.
- Plan limits: developers raise `SubscriptionLimit` (parsed reset time when the tool prints
  one); the loop logs it to `costs.sqlite` (`limits`), waits (30 min retries if unknown, capped
  at `limit_wait_max_hours`), and re-runs the failed step on the same thread.
- A developer with nothing plannable waits while another round is in flight (its end may
  unblock a dependency) and quits when nobody is busy. Planning with an empty todo list makes
  no API call, so these looks are free.

## Rejected

- One long-lived graph with a dynamic task queue: LangGraph's `Send` fan-out joins at the end
  of a superstep, so a new task can't start while another branch runs, and one checkpoint
  thread for 10 hours makes resume and inspection harder.
- Killing the running step on Ctrl+C (the single-run behaviour): it throws away up to an hour
  of a developer session; checkpoints only cover finished steps.

## Consequences

- Areas must stay disjoint between developers (they are: core / app); two loops could
  otherwise plan the same task. ponytail: add a planning lock when two developers share an area.
- The dashboard (`devteam dashboard`) reads the same real state; events now carry a full
  timestamp (`ts`) for "time on it".
