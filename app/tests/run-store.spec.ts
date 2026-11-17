import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SidecarClient } from '@/api/client'
import type { ActiveStatus } from '@/api/types'
import { useConfigStore } from '@/stores/config'
import { useRunStore } from '@/stores/run'
import { useSidecarStore } from '@/stores/sidecar'

import schema from './fixtures/schema.json'
import { defaultConfig, fakeTransport, json, sse, streamed } from './helpers'

function status(state: ActiveStatus['state'], extra: Partial<ActiveStatus> = {}): ActiveStatus {
  return {
    state,
    result: state === 'finished' ? 'done' : null,
    output_dir: '/results/one',
    pdfs: 1,
    pid: 1,
    started: '2026-10-16T10:00:00+00:00',
    finished: null,
    exit_code: state === 'finished' ? 0 : null,
    cancel_requested: false,
    events_offset: 0,
    progress: { files: 1, stage: null, done: 0, total: null, warnings: 0, errors: 0, last_message: null, last_error: null, finished: null },
    stderr: [],
    ...extra,
  }
}

const event = (name: string, fields: Record<string, unknown> = {}) => ({ time: '2026-10-16T10:00:01+00:00', event: name, ...fields })

type Route = (init: RequestInit) => Response | Promise<Response>

async function setup(routes: Record<string, Route>) {
  const transport = fakeTransport((path, init) => {
    const key = `${init.method ?? 'GET'} ${path}`
    if (routes[key]) return routes[key](init)
    if (path === '/config/defaults') return json(defaultConfig())
    if (path === '/config/schema') return json(schema)
    if (path === '/config/validate')
      return json({ valid: true, errors: [], config: null, pdfs: ['/docs/a.pdf'], output: { state: 'new', message: '' } })
    return json({ error: 'not_found', message: key }, 404)
  })
  useSidecarStore().client = new SidecarClient(transport)
  const config = useConfigStore()
  await config.load()
  config.addInputs(['/docs/a.pdf'])
  config.setOutputDir('/results/one')
  return { transport, config, run: useRunStore() }
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('run store', () => {
  it('starts a run and follows its events to the end', async () => {
    const { run, transport } = await setup({
      'POST /run': () => json(status('running'), 202),
      'GET /run/events': () =>
        streamed([
          sse('run_started', event('run_started', { files: ['a.pdf'] }), 10),
          sse('stage_started', event('stage_started', { stage: 'triage', total: 2 }), 20),
          sse('page_done', event('page_done', { stage: 'triage', done: 1, total: 2 }), 30),
          sse('end', status('finished')),
        ]),
    })
    expect(await run.start()).toBe(true)
    await vi.waitFor(() => expect(run.status.state).toBe('finished'))
    expect(run.events.map((e) => e.event)).toEqual(['run_started', 'stage_started', 'page_done'])
    expect(run.summary.stages.triage).toMatchObject({ done: 1, total: 2 })
    expect(run.lastEventId).toBe('30')
    expect(run.result).toBe('done')
    expect(run.connection).toBe('idle')
    // The settings it ran with come back after a restart, so it can be continued
    expect(JSON.parse(localStorage.getItem('table-extract.last-run')!).settings.inputs).toEqual(['/docs/a.pdf'])    
    const body = JSON.parse(String(transport.calls.find((c) => c.path === '/run')!.init.body))
    expect(body).toMatchObject({ overwrite: false, config: { output_dir: '/results/one' } })
  })

  it('reconnects with Last-Event-ID when the stream drops', async () => {
    const seen: (string | null)[] = []
    let attempt = 0
    const { run } = await setup({
      'POST /run': () => json(status('running'), 202),
      'GET /run/events': (init) => {
        seen.push(new Headers(init.headers).get('Last-Event-ID'))
        attempt++
        if (attempt === 1) return streamed([sse('page_done', event('page_done', { stage: 'triage', done: 1 }), 40)])
        if (attempt === 2) throw new TypeError('connection reset')
        return streamed([sse('page_done', event('page_done', { stage: 'triage', done: 2 }), 80), sse('end', status('finished'))])
      },
    })
    await run.start()
    await vi.waitFor(() => expect(run.status.state).toBe('finished'), { timeout: 5000 })
    expect(seen).toEqual([null, '40', '40'])
    expect(run.events.map((e) => e.done)).toEqual([1, 2])
  })

  it('asks for a choice when the folder holds a run with other settings', async () => {
    const output = { state: 'conflict', message: 'Different settings.', previous_status: 'done' }
    const { run } = await setup({
      'POST /run': (init) =>
        JSON.parse(String(init.body)).overwrite
          ? json(status('running'), 202)
          : json({ error: 'output_conflict', message: 'Different settings.', output }, 409),
      'GET /run/events': () => streamed([sse('end', status('finished'))]),
    })
    expect(await run.start()).toBe(false)
    expect(run.conflict).toEqual(output)
    expect(run.error).toBeNull()
    expect(await run.start(true)).toBe(true)
    expect(run.conflict).toBeNull()
  })

  it('picks up a run already in progress', async () => {
    const { run } = await setup({
      'POST /run': () => json({ error: 'run_active', message: 'Busy' }, 409),
      'GET /run': () => json(status('running')),
      'GET /run/events': () => streamed([sse('page_done', event('page_done'), 5), sse('end', status('finished'))]),
    })
    expect(await run.start()).toBe(false)
    await vi.waitFor(() => expect(run.status.state).toBe('finished'))
    expect(run.events).toHaveLength(1)
  })

  it('shows other start errors', async () => {
    const { run } = await setup({
      'POST /run': () => json({ error: 'start_failed', message: 'The run could not start: disk full' }, 500),
    })
    expect(await run.start()).toBe(false)
    expect(run.error?.message).toBe('The run could not start: disk full')
  })

  it('cancels the run in progress', async () => {
    const { run, transport } = await setup({
      'POST /run': () => json(status('running'), 202),
      'GET /run/events': () => streamed([sse('page_done', event('page_done'), 5)], { hold: true }),
      'POST /run/cancel': () => json(status('cancelling', { cancel_requested: true })),
    })
    await run.start()
    await vi.waitFor(() => expect(run.events).toHaveLength(1))
    await run.cancel()
    expect(run.status.state).toBe('cancelling')
    expect(run.running).toBe(true)
    expect(transport.calls.some((c) => c.path === '/run/cancel')).toBe(true)
    run.stopFollowing()
  })
})
