import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SidecarClient } from '@/api/client'
import type { Job, ModelOption, ModelServer, Recommendation, ServerRole, Verdict } from '@/api/types'
import { useConfigStore } from '@/stores/config'
import { POLL_MS, useModelsStore } from '@/stores/models'
import { useSidecarStore } from '@/stores/sidecar'

import schema from './fixtures/schema.json'
import { defaultConfig, fakeTransport, json } from './helpers'

const GB = 1024 ** 3
const QWEN_ID = 'unsloth/Qwen3.5-9B-GGUF:Q8_0'
const GEMMA_ID = 'unsloth/gemma-4-12b-it-GGUF:Q4_K_M'

function option(id: string, modelId: string, verdict: Verdict, installed: boolean): ModelOption {
  return {
    id,
    label: id,
    description: `${id} description`,
    repo: modelId.split(':')[0],
    revision: 'abc',
    quant: modelId.split(':')[1],
    context: 20480,
    tested: true,
    model_id: modelId,
    installed,
    download_bytes: 10 * GB,
    estimate: { context: 20480, parts: {}, total_bytes: 11 * GB, verdict },
    benchmark: null,
  }
}

function server(role: ServerRole, modelId: string, state: ModelServer['state'], port: number): ModelServer {
  return {
    role,
    state,
    error: state === 'failed' ? 'llama-server exited (code 1)' : null,
    model_id: modelId,
    alias: 'qwen3.5-9b-q8',
    context: 20480,
    url: `http://127.0.0.1:${port}/v1/chat/completions`,
    port,
    pid: 1,
    started: 0,
    ready_at: null,
    exit_code: null,
    log: state === 'failed' ? ['error: out of memory'] : [],
  }
}

/** A sidecar holding model-management state that the tests move along. */
function fakeSidecar() {
  const state = {
    offline: false,
    runtime: null as string | null,
    installed: new Set<string>(),
    jobs: [] as Job[],
    vision: null as ModelServer | null,
    startRequests: [] as unknown[],
  }

  function recommendation(): Recommendation {
    const known = state.runtime !== null
    const qwen = option('qwen3.5-9b-q8', QWEN_ID, known ? 'gpu' : 'unknown', state.installed.has(QWEN_ID))
    const gemma = option('gemma-4-12b-q4km', GEMMA_ID, known ? 'gpu' : 'unknown', state.installed.has(GEMMA_ID))
    return {
      model: known ? qwen : null,
      reason: known ? 'It fits.' : 'llama.cpp is needed to check this computer’s graphics memory.',
      options: [qwen, gemma],
      memory: { gpu_bytes: 12 * GB, ram_bytes: 16 * GB, unified_memory: true, has_gpu: true, gpu_source: null, known },
    }
  }

  function job(kind: Job['kind'], target: string, label: string): Job {
    const existing = state.jobs.find((j) => j.target === target && j.finished === null)
    if (existing) return existing
    const created: Job = {
      id: String(state.jobs.length + 1),
      kind,
      target,
      label,
      state: 'downloading',
      error: null,
      bytes_total: 10 * GB,
      bytes_done: 0,
      bytes_per_second: null,
      started: 0,
      finished: null,
      result: null,
    }
    state.jobs.push(created)
    return created
  }

  function finish(target: string, outcome: Job['state'] = 'done'): void {
    const found = state.jobs.find((j) => j.target === target && j.finished === null)!
    found.state = outcome
    found.finished = 1
    if (outcome === 'done') {
      if (found.kind === 'runtime') state.runtime = 'installed'
      else state.installed.add(target)
    }
  }

  const servers = () => ({
    servers: { vision: state.vision },
    config:
      state.vision && state.vision.state !== 'stopped'
        ? { server: { url: state.vision.url, model: state.vision.alias } }
        : {},
  })

  const transport = fakeTransport((path, init) => {
    const key = `${init.method ?? 'GET'} ${path}`
    const body = init.body ? JSON.parse(String(init.body)) : null
    const copy = (value: unknown) => json(JSON.parse(JSON.stringify(value)))
    const offline = () => json({ error: 'offline', message: 'The app is set to work offline.' }, 403)
    switch (key) {
      case 'GET /config/defaults':
        return json(defaultConfig())
      case 'GET /config/schema':
        return json(schema)
      case 'POST /config/validate':
        return json({ valid: true, errors: [], config: null, pdfs: [], output: null })
      case 'GET /settings':
        return json({ offline: state.offline, data_dir: '/data' })
      case 'PUT /settings':
        state.offline = body.offline
        if (state.offline) state.jobs.filter((j) => j.finished === null).forEach((j) => finish(j.target, 'cancelled'))
        return json({ offline: state.offline, data_dir: '/data' })
      case 'GET /runtime':
        return json({
          source: state.runtime,
          recommended_variant: 'macos-arm64',
          variants: [{ id: 'macos-arm64', backend: 'metal', label: 'Apple Silicon', download_bytes: 40e6 }],
          job: null,
        })
      case 'POST /runtime/install':
        return state.offline ? offline() : copy(job('runtime', 'llama.cpp b11429 macos-arm64', 'llama.cpp'))
      case 'GET /models/recommendation':
        return json(recommendation())
      case 'GET /models/installed':
        return json({ models: [], disk: { models_bytes: 0, free_bytes: 100 * GB, folder: '/data/models' } })
      case 'POST /downloads': {
        if (state.offline) return offline()
        const target = recommendation().options.find((o) => o.id === body.curated)!.model_id
        return copy(job('model', target, body.curated))
      }
      case 'GET /downloads':
        return copy({ jobs: state.jobs })
      case 'GET /servers':
        return copy(servers())
      case 'POST /servers/start':
        state.startRequests.push(body)
        state.vision = server('vision', body.model_id, 'starting', 8000)
        return copy(servers())
      case 'POST /servers/stop':
        state.vision = state.vision && { ...state.vision, state: 'stopped' }
        return copy(servers())
    }
    const cancel = path.match(/^\/downloads\/(\d+)\/cancel$/)
    if (cancel) {
      const found = state.jobs.find((j) => j.id === cancel[1])!
      finish(found.target, 'cancelled')
      return copy(found)
    }
    return json({ error: 'not_found', message: key }, 404)
  })

  return { state, transport, finish }
}

async function setup(prepare: (state: ReturnType<typeof fakeSidecar>['state']) => void = () => {}) {
  const sidecar = fakeSidecar()
  prepare(sidecar.state)
  useSidecarStore().client = new SidecarClient(sidecar.transport)
  const config = useConfigStore()
  await config.load()
  const models = useModelsStore()
  await models.load()
  return { ...sidecar, config, models }
}

const requests = (calls: { path: string; init: RequestInit }[]) => calls.map((c) => `${c.init.method ?? 'GET'} ${c.path}`)

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  useModelsStore().stopPolling()
  vi.useRealTimers()
})

describe('models store', () => {
  it('installs llama.cpp, follows the job, then gets the recommendation it makes possible', async () => {
    const { models, state, finish, transport } = await setup()
    expect(models.runtimeInstalled).toBe(false)
    expect(models.chosen).toBeNull()
    expect(models.recommendation?.reason).toMatch(/llama.cpp is needed/)

    await models.installRuntime()
    expect(models.runtimeJob?.state).toBe('downloading')
    expect(models.online).toBe(true)

    state.jobs[0].bytes_done = 5 * GB
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(models.runtimeJob?.bytes_done).toBe(5 * GB)

    finish('llama.cpp b11429 macos-arm64')
    transport.calls.length = 0
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(requests(transport.calls)).toEqual(
      expect.arrayContaining(['GET /downloads', 'GET /runtime', 'GET /models/recommendation', 'GET /models/installed']),
    )
    expect(models.runtimeInstalled).toBe(true)
    expect(models.online).toBe(false)
    expect(models.chosen?.id).toBe('qwen3.5-9b-q8')

    // Nothing left to follow: polling stops
    transport.calls.length = 0
    await vi.advanceTimersByTimeAsync(POLL_MS * 3)
    expect(transport.calls).toEqual([])
  })

  it('downloads the chosen model, cancels, resumes, and sees it installed', async () => {
    const { models, finish, transport } = await setup((s) => (s.runtime = 'installed'))
    expect(models.needsDownload).toBe(true)

    await models.download()
    const posted = transport.calls.filter((c) => c.path === '/downloads' && c.init.method === 'POST')
    expect(posted.map((c) => JSON.parse(String(c.init.body)))).toEqual([{ curated: 'qwen3.5-9b-q8' }])
    expect(models.downloading).toBe(true)

    await models.cancelDownloads()
    expect(models.downloading).toBe(false)
    expect(models.downloads.map((d) => d.job?.state)).toEqual(['cancelled'])

    // Resuming starts it again; the sidecar continues from the partial files
    await models.download()
    expect(models.downloads.map((d) => d.job?.state)).toEqual(['downloading'])

    finish(QWEN_ID)
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(models.chosen?.installed).toBe(true)
    expect(models.needsDownload).toBe(false)
  })

  it('does not download while offline, and says why', async () => {
    const { models, transport } = await setup((s) => (s.runtime = 'installed'))
    await models.setOffline(true)
    expect(models.offline).toBe(true)
    expect(transport.calls.find((c) => c.path === '/settings' && c.init.method === 'PUT')).toBeTruthy()

    await models.download()
    expect(models.actionError).toMatch(/offline/)
    expect(models.jobs).toEqual([])
  })

  it('turning offline on reports the downloads it cancelled', async () => {
    const { models } = await setup((s) => (s.runtime = 'installed'))
    await models.download()
    await models.setOffline(true)
    expect(models.online).toBe(false)
    expect(models.downloads.map((d) => d.job?.state)).toEqual(['cancelled'])
  })

  it('starts the servers, waits until ready, and points the run settings at them', async () => {
    const { models, config, state } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
    })
    config.set('server.url', 'http://localhost:9999/v1/chat/completions')

    await models.startServers()
    expect(state.startRequests).toEqual([{ model_id: QWEN_ID }])
    expect(models.serversState).toBe('starting')
    expect(models.ready).toBe(false)

    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(config.get('server.url')).toBe('http://localhost:9999/v1/chat/completions') // not ready yet

    state.vision!.state = 'ready'
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(models.serversState).toBe('ready')
    expect(models.ready).toBe(true)
    expect(config.get('server.url')).toBe('http://127.0.0.1:8000/v1/chat/completions')
    expect(config.get('server.model')).toBe('qwen3.5-9b-q8')
    expect(config.get('server.allow_remote')).toBe(false)

    await models.stopServers()
    expect(models.serversState).toBe('stopped')
    expect(models.ready).toBe(false)
  })

  it('reports a server that fails to start', async () => {
    const { models, state } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
    })
    await models.startServers()
    state.vision = server('vision', QWEN_ID, 'failed', 8000)
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(models.serversState).toBe('failed')
    expect(models.vision?.error).toMatch(/exited/)
  })

  it('starts the model for a run and waits until it serves', async () => {
    const { models, config, state } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
    })
    const first = models.ensureStarted()
    const second = models.ensureStarted() // a second click shares the wait
    await vi.advanceTimersByTimeAsync(0)
    expect(state.startRequests).toEqual([{ model_id: QWEN_ID }])
    expect(models.loadingForRun).toBe(true)

    let done: boolean | null = null
    void first.then((ok) => (done = ok))
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(done).toBeNull() // still loading

    state.vision!.state = 'ready'
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(await first).toBe(true)
    expect(await second).toBe(true)
    expect(models.loadingForRun).toBe(false)
    expect(config.get('server.url')).toBe('http://127.0.0.1:8000/v1/chat/completions')

    // Already up: nothing more to start
    expect(await models.ensureStarted()).toBe(true)
    expect(state.startRequests).toHaveLength(1)
  })

  it('says so when the model it starts for a run fails to load', async () => {
    const { models, state } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
    })
    const ok = models.ensureStarted()
    await vi.advanceTimersByTimeAsync(0)
    state.vision = server('vision', QWEN_ID, 'failed', 8000)
    await vi.advanceTimersByTimeAsync(POLL_MS)
    expect(await ok).toBe(false)
    expect(models.serversState).toBe('failed')
    expect(models.loadingForRun).toBe(false)
  })

  it('picks up servers already running when it loads', async () => {
    const { models, config } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
      s.vision = server('vision', QWEN_ID, 'ready', 8002)
    })
    expect(models.ready).toBe(true)
    expect(config.get('server.url')).toBe('http://127.0.0.1:8002/v1/chat/completions')
  })

  it('leaves the settings alone when the user brings their own server', async () => {
    const { models, config } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID])
      s.vision = server('vision', QWEN_ID, 'ready', 8002)
    })
    models.setMode('own')
    config.set('server.url', 'http://localhost:9999/v1/chat/completions')
    await models.load()
    expect(config.get('server.url')).toBe('http://localhost:9999/v1/chat/completions')
    expect(models.ready).toBe(true)
    expect(localStorage.getItem('table-extract.model-mode')).toBe('"own"')

    models.setMode('app')
    expect(config.get('server.url')).toBe('http://127.0.0.1:8002/v1/chat/completions')
  })

  it('remembers another choice, and switches the servers to it', async () => {
    const { models, state } = await setup((s) => {
      s.runtime = 'installed'
      s.installed = new Set([QWEN_ID, GEMMA_ID])
      s.vision = server('vision', QWEN_ID, 'ready', 8000)
    })
    models.choose('gemma-4-12b-q4km')
    expect(models.chosen?.id).toBe('gemma-4-12b-q4km')
    expect(models.runningChosen).toBe(false)
    expect(models.ready).toBe(false)
    expect(localStorage.getItem('table-extract.model-choice')).toBe('"gemma-4-12b-q4km"')

    await models.startServers()
    expect(state.startRequests).toEqual([{ model_id: GEMMA_ID }])

    // Choosing the recommendation again follows it rather than pinning it
    models.choose('qwen3.5-9b-q8')
    expect(models.choiceId).toBeNull()
    expect(localStorage.getItem('table-extract.model-choice')).toBeNull()
  })
})
