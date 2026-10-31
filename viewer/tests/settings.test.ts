import { describe, expect, it } from 'vitest'
import { DEFAULTS, loadSettings, mergeSettings, saveSettings } from '../src/settings'

const memory = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } }

describe('settings', () => {
  it('falls back to defaults on bad input', () => {
    expect(mergeSettings(null)).toEqual(DEFAULTS)
    expect(mergeSettings({ filters: { code: 'yes' }, forces: { repel: 'x' } })).toEqual(DEFAULTS)
  })
  it('keeps valid stored values and drops unknown keys', () => {
    const s = mergeSettings({ display: { bloom: 2.5, nope: 1 }, groups: [{ query: 'arv', color: '#fff' }, { bad: 1 }], areaColors: { 'area:a': '#123456' } })
    expect(s.display.bloom).toBe(2.5)
    expect((s.display as unknown as Record<string, unknown>).nope).toBeUndefined()
    expect(s.groups).toEqual([{ query: 'arv', color: '#fff' }])
    expect(s.areaColors).toEqual({ 'area:a': '#123456' })
  })
  it('round-trips through storage and survives broken JSON', () => {
    const st = memory()
    const s = mergeSettings({ forces: { cohesion: 1.4 } })
    saveSettings(s, st)
    expect(loadSettings(st).forces.cohesion).toBe(1.4)
    st.setItem('neurotree.settings.v1', '{broken')
    expect(loadSettings(st)).toEqual(DEFAULTS)
    expect(loadSettings(null)).toEqual(DEFAULTS)
  })
})
