import { describe, expect, it } from 'vitest'

import type { RunEvent } from '@/api/types'
import { formatDuration, summarize } from '@/stores/progress'

const at = (seconds: number) => new Date(Date.UTC(2026, 9, 16, 10, 0, seconds)).toISOString()

function ev(event: string, seconds: number, fields: Partial<RunEvent> = {}): RunEvent {
  return { time: at(seconds), event, ...fields }
}

describe('summarize', () => {
  it('follows stages and their pages', () => {
    const summary = summarize([
      ev('run_started', 0, { files: ['a.pdf', 'b.pdf'] }),
      ev('stage_started', 4, { stage: 'triage', total: 5 }),
      ev('page_done', 14, { stage: 'triage', done: 1, total: 5 }),
      ev('page_done', 24, { stage: 'triage', done: 2, total: 5 }),
    ])
    expect(summary.files).toEqual(['a.pdf', 'b.pdf'])
    expect(summary.current).toBe('triage')
    expect(summary.stages.triage).toMatchObject({ state: 'running', done: 2, total: 5 })
    // 20 s for 2 pages: 3 pages left take about 30 s
    expect(summary.stages.triage.remaining).toBe(30)
    expect(summary.stages.extraction.state).toBe('waiting')
  })

  it('marks stages turned off in the settings, or never started before a later one', () => {
    expect(summarize([], { triage: false }).stages.triage.state).toBe('skipped')
    const summary = summarize([ev('stage_started', 1, { stage: 'extraction', total: 3 })])
    expect(summary.stages.triage.state).toBe('skipped')
  })

  it('collects warnings and errors', () => {
    const summary = summarize([
      ev('warning', 1, { message: ' Slow server ' }),
      ev('error', 2, { message: 'Page 3 error', file: 'a.pdf', page: 3 }),
      ev('log', 3, { message: 'just a log line' }),
    ])
    expect(summary.notices).toEqual([
      { kind: 'warning', message: 'Slow server', file: undefined, page: undefined, time: at(1) },
      { kind: 'error', message: 'Page 3 error', file: 'a.pdf', page: 3, time: at(2) },
    ])
  })

  it('shows a cancelled stage as stopped, with the finish record', () => {
    const summary = summarize([
      ev('stage_started', 1, { stage: 'triage', total: 4 }),
      ev('page_done', 2, { stage: 'triage', done: 1, total: 4 }),
      ev('run_finished', 3, { status: 'cancelled', failed_pages: 0, outputs: { triage: '/out/triage.csv' } }),
    ])
    expect(summary.stages.triage.state).toBe('stopped')
    expect(summary.stages.triage.remaining).toBeNull()
    expect(summary.finished).toEqual({ status: 'cancelled', failed_pages: 0, outputs: { triage: '/out/triage.csv' } })
  })

  it('marks stages that never ran as skipped once the run is done', () => {
    const summary = summarize([
      ev('stage_started', 1, { stage: 'triage', total: 0 }),
      ev('stage_done', 1, { stage: 'triage' }),
      ev('run_finished', 2, { status: 'done' }),
    ])
    expect(summary.stages.triage.state).toBe('done')
    expect(summary.stages.extraction.state).toBe('skipped')
  })
})

describe('formatDuration', () => {
  it.each([
    [20, 'less than a minute'],
    [60, '1 minute'],
    [150, '3 minutes'],
    [3600, '1 hour'],
    [5400, '1 hour 30 minutes'],
  ])('%i s is %s', (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text)
  })
})
