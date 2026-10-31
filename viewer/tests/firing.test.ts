import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { chainToCore, intensity, pickBurst, Replay } from '../src/firing'
import type { GEvent } from '../src/types'
import { tiny } from './data.test'

const DAY = 86400

describe('firing', () => {
  it('walks from a node to the core', () => {
    expect(chainToCore(buildIndex(tiny), 'fn:/x/p/a.py#run')).toEqual(['fn:/x/p/a.py#run', 'file:/x/p/a.py', 'project:p', 'area:a', 'self'])
  })
  it('caps bursts by weight and scales intensity by importance', () => {
    const evs: GEvent[] = [['a', 1, 'read', 1], ['b', 2, 'edit', 3], ['c', 3, 'mention', 0.5]]
    expect(pickBurst(evs, 2).map((e) => e[0])).toEqual(['b', 'a'])
    const ix = buildIndex(tiny)
    expect(intensity(ix.byId.get('file:/x/p/b.py'), 3)).toBeGreaterThan(intensity(ix.byId.get('file:/x/p/a.py'), 3))
    expect(intensity(undefined)).toBeGreaterThan(0)
  })
  it('replays events in order over the chosen duration', () => {
    const evs: GEvent[] = Array.from({ length: 10 }, (_, i) => [`n${i}`, 1000 * DAY + i * DAY, 'edit', 1] as GEvent)
    const r = new Replay(evs, 10)
    expect(r.start).toBe(1000 * DAY)
    expect(r.step(1000)).toEqual([])  // not playing yet
    r.playing = true
    const first = r.step(5000)
    expect(first.map((e) => e[0])).toEqual(['n0', 'n1', 'n2', 'n3', 'n4', 'n5'])
    expect(r.progress()).toBeCloseTo(0.5)
    expect(r.step(5000).map((e) => e[0])).toEqual(['n6', 'n7', 'n8', 'n9'])
    expect(r.playing).toBe(false)
    r.seek(0.25)
    r.playing = true
    expect(r.step(1)[0]?.[0]).toBe('n3')
  })
})
