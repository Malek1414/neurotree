/** Talks to the Tauri shell when running inside it; otherwise reads /graph.json from the Vite dev server. */
export interface IndexStatus { running: boolean; finished_at: number | null; error: string | null }
type Unlisten = () => void

export const isTauri = (): boolean => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
const core = () => import('@tauri-apps/api/core')
const events = () => import('@tauri-apps/api/event')

export async function loadGraphText(search = ''): Promise<string> {
  if (isTauri()) return (await core()).invoke<string>('load_graph')
  const fixture = new URLSearchParams(search).get('fixture')
  const res = await fetch(fixture ? `/graph.json?fixture=${encodeURIComponent(fixture)}` : '/graph.json')
  if (!res.ok) throw new Error(`graph.json: HTTP ${res.status}`)
  return res.text()
}

export async function openPath(path: string): Promise<void> {
  if (isTauri()) await (await core()).invoke('open_path', { path })
}

export async function revealPath(path: string): Promise<void> {
  if (isTauri()) await (await core()).invoke('reveal_path', { path })
}

export async function reindex(): Promise<boolean> {
  return isTauri() ? (await core()).invoke<boolean>('reindex') : false
}

export async function onFsChange(cb: (paths: string[], source: string) => void): Promise<Unlisten> {
  if (!isTauri()) return () => {}
  return (await events()).listen<{ paths: string[]; source: string }>('fs-change', (e) => cb(e.payload.paths, e.payload.source))
}

export async function onIndexStatus(cb: (s: IndexStatus) => void): Promise<Unlisten> {
  if (!isTauri()) return () => {}
  return (await events()).listen<IndexStatus>('index-status', (e) => cb(e.payload))
}
