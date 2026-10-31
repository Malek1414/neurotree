import { describe, expect, it } from 'vitest'
import { buildIndex } from '../src/data'
import { DEFAULTS } from '../src/settings'
import { visibleIds, type ViewState } from '../src/visibility'
import { tiny } from './data.test'

const ix = buildIndex(tiny)
const base: ViewState = { expanded: new Set(), selected: null, local: null, query: '' }
const ids = (s: Set<string>) => [...s].sort()

describe('visibleIds', () => {
  it('shows levels 0-2 and lod-0 files, details only for the selected file', () => {
    const v = visibleIds(tiny, ix, base, DEFAULTS.filters)
    expect(v.has('fn:/x/p/a.py#run')).toBe(false)
    expect(v.has('file:/x/p/a.py') && v.has('project:q') && v.has('self')).toBe(true)
    expect(visibleIds(tiny, ix, { ...base, selected: 'file:/x/p/a.py' }, DEFAULTS.filters).has('fn:/x/p/a.py#run')).toBe(true)
  })
  it('applies type filters and keeps ancestors for search hits', () => {
    const noCode = visibleIds(tiny, ix, base, { ...DEFAULTS.filters, code: false })
    expect(noCode.has('file:/x/p/a.py')).toBe(false)
    const hit = visibleIds(tiny, ix, { ...base, query: 'b.py' }, DEFAULTS.filters)
    expect(ids(hit)).toEqual(['area:a', 'file:/x/p/b.py', 'project:p', 'self'])
  })
  it('local graph is a BFS over every edge kind', () => {
    const local = visibleIds(tiny, ix, { ...base, local: { id: 'file:/x/p/a.py', depth: 1 } }, DEFAULTS.filters)
    expect(ids(local)).toEqual(['file:/x/p/a.py', 'file:/x/p/b.py', 'fn:/x/p/a.py#run', 'project:p'])
  })
})
