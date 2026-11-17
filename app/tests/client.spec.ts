import { describe, expect, it, vi } from 'vitest'

import { ApiError, SidecarClient } from '@/api/client'
import { browserTransport } from '@/api/transports'

import { defaultConfig, fakeTransport, json, sse, streamed } from './helpers'

describe('SidecarClient', () => {
  it('posts the config to validate and returns the answer', async () => {
    const transport = fakeTransport(() => json({ valid: true, errors: [], config: null, pdfs: [], output: null }))
    const client = new SidecarClient(transport)
    const config = defaultConfig()
    await client.validate(config)
    const [call] = transport.calls
    expect(call.path).toBe('/config/validate')
    expect(call.init.method).toBe('POST')
    expect(new Headers(call.init.headers).get('Content-Type')).toBe('application/json')
    expect(JSON.parse(String(call.init.body))).toEqual(config)
  })

  it('starts a run with the overwrite flag', async () => {
    const transport = fakeTransport(() => json({ state: 'running' }, 202))
    await new SidecarClient(transport).start(defaultConfig(), true)
    const body = JSON.parse(String(transport.calls[0].init.body))
    expect(transport.calls[0].path).toBe('/run')
    expect(body.overwrite).toBe(true)
    expect(body.config.server.model).toBe('qwen')
  })

  it('turns error answers into ApiError with code, message, field errors and body', async () => {
    const output = { state: 'conflict', message: 'Different settings.' }
    const client = new SidecarClient(
      fakeTransport(() => json({ error: 'output_conflict', message: 'Different settings.', output }, 409)),
    )
    const error = await client.start(defaultConfig()).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 409, code: 'output_conflict', message: 'Different settings.' })
    expect((error as ApiError).body?.output).toEqual(output)
  })

  it('keeps the field errors of invalid settings', async () => {
    const errors = [{ field: 'triage.dpi', loc: ['triage', 'dpi'], message: 'Too low', type: 'greater_than_equal' }]
    const client = new SidecarClient(fakeTransport(() => json({ error: 'invalid_config', message: 'x', errors }, 422)))
    const error = (await client.start(defaultConfig()).catch((e: unknown) => e)) as ApiError
    expect(error.errors).toEqual(errors)
  })

  it('reports an unreachable sidecar as status 0', async () => {
    const client = new SidecarClient(
      fakeTransport(() => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const error = (await client.health().catch((e: unknown) => e)) as ApiError
    expect(error.unreachable).toBe(true)
    expect(error.code).toBe('unreachable')
  })

  it('handles an error answer that is not JSON', async () => {
    const client = new SidecarClient(fakeTransport(() => new Response('Bad gateway', { status: 502 })))
    const error = (await client.status().catch((e: unknown) => e)) as ApiError
    expect(error).toMatchObject({ status: 502, code: 'http_error' })
  })

  it('reads the event stream, sending Last-Event-ID', async () => {
    const transport = fakeTransport(() =>
      streamed([sse('page_done', { event: 'page_done', done: 1 }, 100), sse('end', { state: 'finished' })]),
    )
    const messages = []
    for await (const message of new SidecarClient(transport).events({ lastEventId: '50' })) messages.push(message)
    expect(new Headers(transport.calls[0].init.headers).get('Last-Event-ID')).toBe('50')
    expect(messages.map((m) => [m.event, m.id])).toEqual([
      ['page_done', '100'],
      ['end', '100'],
    ])
  })

  it('reports no_run when no run was started', async () => {
    const client = new SidecarClient(fakeTransport(() => json({ error: 'no_run', message: 'No run' }, 404)))
    const stream = client.events()
    await expect(stream.next()).rejects.toMatchObject({ code: 'no_run' })
  })
})

describe('browserTransport', () => {
  it('sends same-origin requests under /sidecar with the token', async () => {
    const fetcher = vi.fn(async () => json({}))
    const transport = browserTransport('dev-token', '/sidecar', fetcher as unknown as typeof fetch)
    await transport.request('/run', { method: 'POST', headers: { 'Content-Type': 'application/json' } })
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    const headers = new Headers(init.headers)
    expect(url).toBe('/sidecar/run')
    expect(headers.get('Authorization')).toBe('Bearer dev-token')
    expect(headers.get('Content-Type')).toBe('application/json')
    expect(init.method).toBe('POST')
  })
})

describe('SidecarClient model management', () => {
  it('changes the offline setting with PUT', async () => {
    const transport = fakeTransport(() => json({ offline: true, data_dir: '/data' }))
    const settings = await new SidecarClient(transport).setOffline(true)
    expect(settings.offline).toBe(true)
    expect(transport.calls[0]).toMatchObject({ path: '/settings', init: { method: 'PUT' } })
    expect(JSON.parse(String(transport.calls[0].init.body))).toEqual({ offline: true })
  })

  it('deletes a model by id, escaped in the query', async () => {
    const transport = fakeTransport(() => json({ models: [], disk: { models_bytes: 0, free_bytes: 1, folder: '/m' } }))
    await new SidecarClient(transport).deleteModel('unsloth/Qwen3.5-9B-GGUF:Q8_0')
    expect(transport.calls[0].path).toBe('/models/installed?id=unsloth%2FQwen3.5-9B-GGUF%3AQ8_0')
    expect(transport.calls[0].init.method).toBe('DELETE')
  })

  it('starts downloads and installs, lists and cancels jobs', async () => {
    const job = { id: '3', state: 'downloading' }
    const transport = fakeTransport((path) => json(path === '/downloads' ? { jobs: [job] } : job, 202))
    const client = new SidecarClient(transport)
    await client.download({ curated: 'qwen3.5-9b-q8' })
    await client.installRuntime()
    expect(await client.jobs()).toEqual([job])
    await client.cancelJob('3')
    const bodies = transport.calls.map((c) => c.init.body && JSON.parse(String(c.init.body)))
    expect(transport.calls.map((c) => `${c.init.method ?? 'GET'} ${c.path}`)).toEqual([
      'POST /downloads',
      'POST /runtime/install',
      'GET /downloads',
      'POST /downloads/3/cancel',
    ])
    expect(bodies[0]).toEqual({ curated: 'qwen3.5-9b-q8' })
    expect(bodies[1]).toEqual({ variant: null })
  })

  it('starts and stops the model servers', async () => {
    const transport = fakeTransport(() => json({ servers: { vision: null }, config: {} }, 202))
    const client = new SidecarClient(transport)
    await client.startServers({ model_id: 'org/M:Q8_0' })
    await client.stopServers()
    expect(transport.calls.map((c) => c.path)).toEqual(['/servers/start', '/servers/stop'])
    expect(JSON.parse(String(transport.calls[0].init.body))).toEqual({ model_id: 'org/M:Q8_0' })
  })

  it('reports the offline refusal of a download', async () => {
    const client = new SidecarClient(
      fakeTransport(() => json({ error: 'offline', message: 'The app is set to work offline.' }, 403)),
    )
    const error = (await client.download({ curated: 'qwen3.5-9b-q8' }).catch((e: unknown) => e)) as ApiError
    expect(error).toMatchObject({ status: 403, code: 'offline' })
  })
})
