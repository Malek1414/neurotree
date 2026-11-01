/// <reference types="vitest/config" />
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { defineConfig } from 'vite'

const here = path.dirname(new URL(import.meta.url).pathname)
const dataGraph = process.env.NEUROTREE_GRAPH ?? path.join(os.homedir(), 'Library/Application Support/Neurotree/graph.json')

export default defineConfig({
  clearScreen: false,
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: { chunkSizeWarningLimit: 2000 },
  plugins: [{
    name: 'neurotree-graph',
    configureServer(server) {
      server.middlewares.use('/areas.json', (req, res) => {  // dev only: same file the Tauri shell reads/writes
        const file = path.join(path.dirname(dataGraph), 'areas.json')
        if (req.method === 'POST') {
          let body = ''
          req.on('data', (c) => { body += c; if (body.length > 2_000_000) req.destroy() })
          req.on('end', () => {
            try {
              const v = JSON.parse(body)
              if (!v || typeof v !== 'object' || typeof v.areas !== 'object') throw new Error('missing "areas"')
            } catch (e) { res.statusCode = 400; res.end(String(e)); return }
            fs.writeFileSync(`${file}.tmp`, body)
            fs.renameSync(`${file}.tmp`, file)
            res.end('ok')
          })
          return
        }
        if (!fs.existsSync(file)) { res.statusCode = 404; res.end('missing'); return }
        res.setHeader('Content-Type', 'application/json')
        res.setHeader('Cache-Control', 'no-store')
        fs.createReadStream(file).pipe(res)
      })
      server.middlewares.use('/graph.json', (req, res) => {
        const name = new URL(req.url ?? '/', 'http://local').searchParams.get('fixture')
        const file = name && /^[a-z0-9-]+$/.test(name)
          ? path.join(here, 'fixtures', `${name}.json`)
          : fs.existsSync(dataGraph) ? dataGraph : path.join(here, 'fixtures/graph.json')
        if (!fs.existsSync(file)) { res.statusCode = 404; res.end('missing'); return }
        res.setHeader('Content-Type', 'application/json')
        res.setHeader('Cache-Control', 'no-store')
        fs.createReadStream(file).pipe(res)
      })
    },
  }],
  test: { include: ['tests/**/*.test.ts'], environment: 'node' },
})
