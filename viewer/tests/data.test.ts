import { describe, expect, it } from 'vitest'
import { buildIndex, resolvePath } from '../src/data'
import type { GraphData, GNode } from '../src/types'

const n = (o: Partial<GNode> & Pick<GNode, 'id' | 'level'>): GNode => ({
  kind: 'file', parent: null, label: o.id, path: null, project: null, lod: 0, color: null, type: null, importance: 0,
  attentionRank: 0, signals: { attention: 0, history: 0, structure: null, runtime: null }, noise: 1, evidence: [],
  lastActivity: null, ...o,
})
export const tiny: GraphData = {
  meta: { generated_at: 1000 * 86400, counts: {}, status: {} },
  nodes: [
    n({ id: 'self', level: 0, kind: 'self', importance: 1 }),
    n({ id: 'area:a', level: 1, kind: 'area', parent: 'self', color: '#f5a524' }),
    n({ id: 'project:p', level: 2, kind: 'project', parent: 'area:a', path: '/x/p', importance: 0.9, attentionRank: 0.9 }),
    n({ id: 'project:q', level: 2, kind: 'project', parent: 'area:a', path: '/x/p/sub', importance: 0.2, attentionRank: 0.2 }),
    n({ id: 'file:/x/p/a.py', level: 3, type: 'code', parent: 'project:p', project: 'project:p', path: '/x/p/a.py', importance: 0.3 }),
    n({ id: 'file:/x/p/b.py', level: 3, type: 'code', parent: 'project:p', project: 'project:p', path: '/x/p/b.py', importance: 0.8 }),
    n({ id: 'fn:/x/p/a.py#run', level: 4, kind: 'function', parent: 'file:/x/p/a.py', path: '/x/p/a.py' }),
  ],
  edges: [
    { source: 'self', target: 'area:a', kind: 'tree', weight: 1 },
    { source: 'area:a', target: 'project:p', kind: 'tree', weight: 1 },
    { source: 'project:p', target: 'file:/x/p/a.py', kind: 'tree', weight: 1 },
    { source: 'file:/x/p/a.py', target: 'fn:/x/p/a.py#run', kind: 'tree', weight: 1 },
    { source: 'file:/x/p/a.py', target: 'file:/x/p/b.py', kind: 'import', weight: 1 },
  ],
  events: [['file:/x/p/a.py', 995 * 86400, 'edit', 3], ['file:/x/p/b.py', 990 * 86400, 'read', 1], ['file:/x/p/a.py', 900 * 86400, 'commit', 2]],
}

describe('buildIndex', () => {
  const ix = buildIndex(tiny)
  it('maps parents, areas and neighbors', () => {
    expect(ix.children.get('project:p')!.map((c) => c.id)).toEqual(['file:/x/p/b.py', 'file:/x/p/a.py'])
    expect(ix.areaOf.get('fn:/x/p/a.py#run')).toBe('area:a')
    expect(ix.areaOf.has('self')).toBe(false)
    expect([...ix.neighbors.get('file:/x/p/a.py')!].sort()).toEqual(['file:/x/p/b.py', 'fn:/x/p/a.py#run', 'project:p'])
  })
  it('rolls activity and first-seen up to ancestors', () => {
    expect(ix.activity7d.get('file:/x/p/a.py')).toBe(3)
    expect(ix.activity7d.get('project:p')).toBe(3)
    expect(ix.activity7d.get('self')).toBe(3)
    expect(ix.firstSeen.get('project:p')).toBe(900 * 86400)
  })
  it('resolves live paths to files, then to the deepest project', () => {
    expect(resolvePath(ix, '/x/p/a.py')).toBe('file:/x/p/a.py')
    expect(resolvePath(ix, '/x/p/sub/new.md')).toBe('project:q')
    expect(resolvePath(ix, '/x/p/new.md')).toBe('project:p')
    expect(resolvePath(ix, '/elsewhere')).toBeNull()
  })
})
