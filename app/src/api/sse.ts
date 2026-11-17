// Server-Sent Events read from a fetch body. A browser EventSource cannot send
// the sidecar's Authorization header, and Tauri's HTTP plugin only gives a
// fetch Response, so the stream is parsed here, following the HTML spec's
// event stream interpretation (fields event, data, id; comments; CR, LF or
// CRLF line ends).

export interface SseMessage {
  event: string
  data: string
  /** The stream's last event id when this message was dispatched. */
  id: string
}

export class SseParser {
  private buffer = ''
  private eventType = ''
  private data: string[] = []
  private hasData = false
  lastEventId = ''

  /** Feed decoded text; returns the messages it completed. */
  push(text: string): SseMessage[] {
    this.buffer += text
    const messages: SseMessage[] = []
    let start = 0
    for (;;) {
      const lf = this.buffer.indexOf('\n', start)
      const cr = this.buffer.indexOf('\r', start)
      let end: number
      let next: number
      if (cr >= 0 && (lf < 0 || cr < lf)) {
        if (cr === this.buffer.length - 1) break // a CRLF may be split across chunks
        end = cr
        next = this.buffer[cr + 1] === '\n' ? cr + 2 : cr + 1
      } else if (lf >= 0) {
        end = lf
        next = lf + 1
      } else {
        break
      }
      const message = this.line(this.buffer.slice(start, end))
      if (message) messages.push(message)
      start = next
    }
    this.buffer = this.buffer.slice(start)
    return messages
  }

  private line(line: string): SseMessage | null {
    if (line === '') return this.dispatch()
    if (line.startsWith(':')) return null
    const colon = line.indexOf(':')
    const name = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (name === 'event') this.eventType = value
    else if (name === 'data') {
      this.data.push(value)
      this.hasData = true
    } else if (name === 'id' && !value.includes('\0')) this.lastEventId = value
    return null
  }

  private dispatch(): SseMessage | null {
    const message = this.hasData
      ? { event: this.eventType || 'message', data: this.data.join('\n'), id: this.lastEventId }
      : null
    this.eventType = ''
    this.data = []
    this.hasData = false
    return message
  }
}

/** The messages of an event stream, until it ends. An unfinished last message is dropped. */
export async function* readSse(body: ReadableStream<Uint8Array>, lastEventId = ''): AsyncGenerator<SseMessage> {
  const parser = new SseParser()
  parser.lastEventId = lastEventId
  const decoder = new TextDecoder()
  const reader = body.getReader()
  let ended = false
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      yield* parser.push(decoder.decode(value, { stream: true }))
    }
    ended = true
    yield* parser.push(decoder.decode())
  } finally {
    if (!ended) await reader.cancel().catch(() => undefined) // the caller stopped early: close the connection
    reader.releaseLock()
  }
}
