import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { coneDirections, computeTargets, fibonacciSphere, targetRadius, type Vec } from '../src/layout'
import { DEFAULTS } from '../src/settings'
import { tiny } from './data.test'

const len = (v: Vec) => Math.hypot(...v)
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

describe('layout', () => {
  it('spreads unit directions over the sphere', () => {
    const dirs = fibonacciSphere(12)
    expect(dirs).toHaveLength(12)
    for (const d of dirs) expect(len(d)).toBeCloseTo(1, 6)
    let minAngle = Math.PI
    for (let i = 0; i < dirs.length; i++) for (let j = i + 1; j < dirs.length; j++) minAngle = Math.min(minAngle, Math.acos(Math.min(1, dot(dirs[i], dirs[j]))))
    expect(minAngle).toBeGreaterThan(0.5)
  })
  it('keeps cone directions inside the cone', () => {
    const axis: Vec = [0, 0.6, 0.8]
    const dirs = coneDirections(axis, 25, 0.4)
    for (const d of dirs) { expect(len(d)).toBeCloseTo(1, 6); expect(dot(d, axis)).toBeGreaterThanOrEqual(Math.cos(0.4) - 1e-9) }
    expect(coneDirections(axis, 1, 0.4)[0].map((x) => +x.toFixed(6))).toEqual(axis)
  })
  it('puts hot nodes closer to the core within a level', () => {
    expect(targetRadius(0, 1)).toBe(0)
    expect(targetRadius(2, 1)).toBeLessThan(targetRadius(2, 0))
    expect(targetRadius(3, 1)).toBeGreaterThan(targetRadius(2, 0) * 0.9)
  })
  it('computes targets for every node', () => {
    const ix = buildIndex(tiny)
    const t = computeTargets(ix, DEFAULTS.forces)
    expect(t.get('self')).toEqual([0, 0, 0])
    expect(t.size).toBe(tiny.nodes.length)
    const p = t.get('project:p')!, q = t.get('project:q')!, a = t.get('area:a')!
    expect(len(p)).toBeLessThan(len(q))
    expect(dot(p, a) / (len(p) * len(a))).toBeGreaterThan(Math.cos(1.2))
  })
})
