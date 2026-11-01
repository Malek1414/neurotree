import { describe, expect, it } from 'vitest'
import { applyAreas, mergeAreas, moveProject, parseAreas, renameArea, type AreasFile } from '../src/areas'
import { buildIndex } from '../src/data'
import type { GraphData } from '../src/types'
import { tiny } from './data.test'

const file = (): AreasFile => ({
  reviewed: false,
  areas: {
    hack: { name: 'Hack', color: '#f5a524', projects: ['project:p'] },
    uni: { name: 'Uni', color: '#5b8def', projects: ['project:q'] },
    unsorted: { name: 'Unsorted', color: '#6b7280', projects: [] },
  },
})

describe('areas', () => {
  it('parses valid files and rejects broken ones', () => {
    expect(parseAreas(JSON.stringify(file()))?.areas.hack.name).toBe('Hack')
    expect(parseAreas('{nope')).toBeNull()
    expect(parseAreas(JSON.stringify({ areas: { x: { name: 1 } } }))).toBeNull()
    expect(parseAreas(JSON.stringify({ areas: { x: { name: 'X', color: '#fff', projects: [3] } } }))).toBeNull()
  })
  it('moves a project, renames and merges, always marking the file reviewed', () => {
    let a = moveProject(file(), 'project:q', 'hack')
    expect(a.areas.hack.projects).toEqual(['project:p', 'project:q'])
    expect(a.areas.uni.projects).toEqual([])
    expect(a.reviewed).toBe(true)
    a = renameArea(a, 'hack', '  Hackathons  ')
    expect(a.areas.hack.name).toBe('Hackathons')
    expect(renameArea(a, 'hack', '   ').areas.hack.name).toBe('Hackathons')
    a = mergeAreas(moveProject(a, 'project:q', 'uni'), 'uni', 'hack')
    expect(a.areas.uni).toBeUndefined()
    expect(a.areas.hack.projects.sort()).toEqual(['project:p', 'project:q'])
    expect(() => mergeAreas(a, 'unsorted', 'hack')).toThrow()
    expect(file().reviewed).toBe(false)  // inputs are never mutated
  })
  it('applies areas to the loaded graph without a reindex', () => {
    const data: GraphData = structuredClone(tiny)
    const a = moveProject(file(), 'project:q', 'hack')
    a.areas.hack.name = 'Hackathons'
    const changed = applyAreas(data, a)
    expect(changed).toBe(true)
    const ix = buildIndex(data)
    expect(ix.byId.get('area:hack')?.label).toBe('Hackathons')
    expect(ix.byId.get('project:q')?.parent).toBe('area:hack')
    expect(ix.areaOf.get('file:/x/p/a.py')).toBe('area:hack')
    expect(data.edges.some((e) => e.kind === 'tree' && e.source === 'area:hack' && e.target === 'project:q')).toBe(true)
    expect(data.edges.some((e) => e.kind === 'tree' && e.target === 'project:q' && e.source !== 'area:hack')).toBe(false)
  })
})
