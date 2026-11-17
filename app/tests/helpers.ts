import type { Transport } from '@/api/client'
import type { RunConfig } from '@/api/types'

import defaults from './fixtures/defaults.json'

export function defaultConfig(): RunConfig {
  return JSON.parse(JSON.stringify(defaults)) as RunConfig
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

/** A response whose body streams the given chunks, then ends (or stays open with hold). */
export function streamed(chunks: string[], options: { hold?: boolean } = {}): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      if (!options.hold) controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

export interface Call {
  path: string
  init: RequestInit
}

type Handler = (path: string, init: RequestInit) => Response | Promise<Response>

/** A Transport answering from a handler, recording every request. */
export function fakeTransport(handler: Handler): Transport & { calls: Call[] } {
  const calls: Call[] = []
  return {
    calls,
    async request(path, init = {}) {
      calls.push({ path, init })
      return handler(path, init)
    },
  }
}

export function sse(event: string, data: unknown, id?: number): string {
  return `${id === undefined ? '' : `id: ${id}\n`}event: ${event}\ndata: ${JSON.stringify(data)}\n\n`
}

export function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0))
}
