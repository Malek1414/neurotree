# Keeping Neurotree live without draining the Mac

## Where the energy went

Measured on this Mac (1 Nov 2026, on battery, Low Power Mode on):

- **The indexer.** Every change anywhere under `~/Desktop` or `~/Documents` (a render writing frames,
  `npm install`, a Claude edit) marked the index dirty, and 10 minutes later the whole Mac was indexed again
  from scratch: 117 repos opened with ~390 `git` processes, a year of `git log` per repo, every module parsed,
  every Claude session log re-read: **316 s and 167 s of CPU per run**, at normal priority on the performance cores.
- **The viewer.** After every index run the window reloaded: 60 MB of JSON parsed and the whole tree laid
  out again, also while the window was hidden. Every Claude edit woke the 3D renderer for 20 s, also while hidden.
  Over 9 hours the WebKit processes used 2 h 48 min of CPU.
- Idle with nothing changing: ~0 % (the watcher and the 20 s render pause already worked).

## What changed

| Change | Effect |
|---|---|
| `cache.db`: a repo is redone only when its HEAD or its uncommitted files change; a session log only when it grew; a note's mentions only when it changed; the GitHub list at most hourly | a reindex after ordinary work: **71 s and 55 s of CPU** (3× less CPU), 76 of 78 repos and 1,046 of 1,047 session logs reused. The first run fills the cache once: 199 s of CPU. |
| HEAD, remote and newest-commit time read from `.git` files and cached per commit, instead of ~234 `git` processes per run | 117 repos in 0.11 s instead of ~14 s |
| The privacy check (`is_excluded`, called for every path) rewritten with plain string checks | 1.5 ms → 11 µs per path (~140×) |
| The indexer lowers itself to Darwin background priority (efficiency cores, throttled disk I/O; `git` children inherit it) | the same work at a fraction of the power; it yields to anything you are doing |
| The app asks `pmset` (only when a reindex is due): on battery it waits 30 min instead of 10, in Low Power Mode 60 | 3× / 6× fewer runs away from the charger |
| A hidden window does not reload after an index run (it reloads once when you look again) and does not fire on changes | no 60 MB parse + layout nobody sees |

## Further ideas, not built yet (cheapest first)

1. **Index on the charger only, manual on battery.** One setting; the power check is already there.
2. **Tell the indexer what changed.** The app's file watcher already knows which paths changed; passing them to
   the indexer would skip even the `git status` of the ~75 repos nobody touched (most of a warm run's time).
3. **A smaller `graph.json`.** 60 MB, mostly 100k events that repeat long path ids. Numbering the nodes and
   storing events as `[node number, time, kind, weight]` would cut it ~3× and the viewer's parse time with it.
4. **Send the viewer a diff instead of reloading.** The tree barely changes between runs; a list of changed nodes
   would replace the reload and the full layout.
5. **Ignore build folders at the FSEvents level.** `node_modules`, `target`, `.venv` changes are already dropped,
   but each one still wakes the app; FSEvents exclusion paths (up to 8) would stop them before they arrive.
6. **Drop the window's web view while it is hidden** and recreate it on show: frees the WebKit processes'
   memory and their idle wakeups; costs one second of loading when you open the window.
