export interface Signals { attention: number | null; history: number | null; structure: number | null; runtime: number | null }

export interface GNode {
  id: string
  kind: string
  level: number
  parent: string | null
  label: string
  path: string | null
  project: string | null
  lod: number
  color: string | null
  type: string | null
  importance: number
  attentionRank: number
  signals: Signals
  noise: number
  evidence: string[]
  lastActivity: number | null
}

export interface GEdge { source: string; target: string; kind: string; weight: number }

/** [node id, unix seconds, kind, weight] */
export type GEvent = [string, number, string, number]

export interface GraphData {
  meta: { generated_at: number; counts: Record<string, number>; status: Record<string, unknown>; weights?: Record<string, number> }
  nodes: GNode[]
  edges: GEdge[]
  events: GEvent[]
}
