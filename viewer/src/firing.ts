import type { GraphIndex } from './data'
import type { GEvent, GNode } from './types'

const DAY = 86400

export function chainToCore(index: GraphIndex, id: string): string[] {
  const out: string[] = []
  let cur = index.byId.get(id)
  for (let guard = 0; cur && guard < 8; guard++) {
    out.push(cur.id)
    cur = cur.parent ? index.byId.get(cur.parent) : undefined
  }
  return out
}

export function pickBurst(events: GEvent[], cap: number): GEvent[] {
  return events.length <= cap ? events : [...events].sort((a, b) => b[3] - a[3]).slice(0, cap)
}

/** How hard a node fires: importance first, the event's weight second. */
export function intensity(n: GNode | undefined, weight = 1): number {
  const imp = Math.min(1, 0.25 + 0.75 * (n?.importance ?? 0))
  return imp * Math.min(1, 0.55 + weight / 6)
}

function firstAfter(events: GEvent[], ts: number, inclusive: boolean): number {
  let lo = 0, hi = events.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (inclusive ? events[mid][1] < ts : events[mid][1] <= ts) lo = mid + 1
    else hi = mid
  }
  return lo
}

export class Replay {
  playing = false
  readonly start: number
  readonly end: number
  cursor: number
  private idx: number
  private durationMs: number

  constructor(private events: GEvent[], seconds: number, windowDays = 120) {
    const last = events.length ? events[events.length - 1][1] : 0
    this.end = last
    this.start = events.length ? Math.max(events[0][1], last - windowDays * DAY) : 0
    this.cursor = this.start
    this.idx = firstAfter(events, this.start, true)
    this.durationMs = Math.max(1, seconds) * 1000
  }

  setSeconds(seconds: number): void {
    this.durationMs = Math.max(1, seconds) * 1000
  }

  progress(): number {
    return this.end > this.start ? (this.cursor - this.start) / (this.end - this.start) : 1
  }

  seek(fraction: number): void {
    const f = Math.max(0, Math.min(1, fraction))
    this.cursor = this.start + f * (this.end - this.start)
    this.idx = firstAfter(this.events, this.cursor, true)
  }

  step(dtMs: number, cap = 40): GEvent[] {
    if (!this.playing || this.end <= this.start) return []
    this.cursor = Math.min(this.end, this.cursor + ((this.end - this.start) * dtMs) / this.durationMs)
    const out: GEvent[] = []
    while (this.idx < this.events.length && this.events[this.idx][1] <= this.cursor) out.push(this.events[this.idx++])
    if (this.cursor >= this.end) this.playing = false
    return pickBurst(out, cap)
  }
}
