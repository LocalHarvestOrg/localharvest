import { describe, expect, it } from 'vitest'

import { readSse, SseParser } from '@/api/sse'

import { streamed } from './helpers'

async function collect(response: Response, lastEventId = '') {
  const messages = []
  for await (const message of readSse(response.body!, lastEventId)) messages.push(message)
  return messages
}

describe('SseParser', () => {
  it('dispatches a message on a blank line', () => {
    const parser = new SseParser()
    expect(parser.push('event: page_done\ndata: {"done": 1}\n')).toEqual([])
    expect(parser.push('\n')).toEqual([{ event: 'page_done', data: '{"done": 1}', id: '' }])
  })

  it('defaults the event name to message and joins data lines', () => {
    const parser = new SseParser()
    expect(parser.push('data: a\ndata: b\n\n')).toEqual([{ event: 'message', data: 'a\nb', id: '' }])
  })

  it('keeps the last id for later messages and ignores comments', () => {
    const parser = new SseParser()
    const messages = parser.push(': keep-alive\n\nid: 120\nevent: log\ndata: 1\n\nevent: log\ndata: 2\n\n')
    expect(messages.map((m) => [m.data, m.id])).toEqual([
      ['1', '120'],
      ['2', '120'],
    ])
    expect(parser.lastEventId).toBe('120')
  })

  it('handles CRLF line ends split across chunks', () => {
    const parser = new SseParser()
    expect(parser.push('data: x\r')).toEqual([])
    expect(parser.push('\n\r')).toEqual([])
    expect(parser.push('\n')).toEqual([{ event: 'message', data: 'x', id: '' }])
  })

  it('does not dispatch a block without data', () => {
    const parser = new SseParser()
    expect(parser.push('event: end\n\n')).toEqual([])
  })

  it('takes a field without a colon as an empty value, and keeps colons in the value', () => {
    const parser = new SseParser()
    expect(parser.push('data\ndata: a: b\n\n')).toEqual([{ event: 'message', data: '\na: b', id: '' }])
  })
})

describe('readSse', () => {
  it('reads messages across chunk boundaries and drops an unfinished last one', async () => {
    const response = streamed(['id: 10\nevent: run_st', 'arted\ndata: {"a":', '1}\n\n', 'data: half'])
    expect(await collect(response)).toEqual([{ event: 'run_started', data: '{"a":1}', id: '10' }])
  })

  it('decodes UTF-8 split inside a character', async () => {
    const bytes = new TextEncoder().encode('data: Société\n\n')
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, 13))
        controller.enqueue(bytes.slice(13))
        controller.close()
      },
    })
    const messages = []
    for await (const message of readSse(body)) messages.push(message)
    expect(messages[0].data).toBe('Société')
  })

  it('starts from the given last event id', async () => {
    const [message] = await collect(streamed(['data: x\n\n']), '42')
    expect(message.id).toBe('42')
  })

  it('cancels the stream when the reader stops early', async () => {
    let cancelled = false
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: 1\n\ndata: 2\n\n'))
      },
      cancel() {
        cancelled = true
      },
    })
    for await (const message of readSse(body)) {
      expect(message.data).toBe('1')
      break
    }
    expect(cancelled).toBe(true)
  })
})
