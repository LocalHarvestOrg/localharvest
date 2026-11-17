// Turns the run's events (table_extract/events.py) into what the Run screen
// shows: one line per stage, in plain language.

import type { RunEvent, RunResult, StageName } from '@/api/types'

export const STAGES: StageName[] = ['triage', 'extraction']

export const STAGE_TEXT: Record<StageName, { title: string; description: string; unit: [string, string] }> = {
  triage: {
    title: 'Triage',
    description: 'The model looks at each page and keeps the ones that list owners, directors or subsidiaries.',
    unit: ['page', 'pages'],
  },
  extraction: {
    title: 'Extraction',
    description: 'The model reads each relevant page and writes the companies, people and their relationships to a CSV file.',
    unit: ['page', 'pages'],
  },
}

export type StageState = 'waiting' | 'running' | 'done' | 'skipped' | 'stopped'

export interface StageProgress {
  name: StageName
  state: StageState
  done: number
  total: number | null
  startedAt: number | null
  lastAt: number | null
  /** Seconds left in this stage, once a few pages give a pace. */
  remaining: number | null
}

export interface Notice {
  kind: 'warning' | 'error'
  message: string
  file?: string
  page?: number
  time: string
}

export interface RunSummary {
  files: string[]
  stages: Record<StageName, StageProgress>
  current: StageName | null
  notices: Notice[]
  finished: { status: RunResult; failed_pages: number; outputs: Record<string, string> } | null
}

function time(event: RunEvent): number | null {
  const value = Date.parse(event.time)
  return Number.isNaN(value) ? null : value
}

function emptyStage(name: StageName): StageProgress {
  return { name, state: 'waiting', done: 0, total: null, startedAt: null, lastAt: null, remaining: null }
}

/**
 * enabled says which stages this run's settings turned on (the others show as
 * skipped from the start).
 */
export function summarize(events: RunEvent[], enabled: Partial<Record<StageName, boolean>> = {}): RunSummary {
  const stages = Object.fromEntries(STAGES.map((name) => [name, emptyStage(name)])) as Record<StageName, StageProgress>
  for (const name of STAGES) if (enabled[name] === false) stages[name].state = 'skipped'
  const summary: RunSummary = { files: [], stages, current: null, notices: [], finished: null }

  for (const event of events) {
    const stage = event.stage && stages[event.stage] ? stages[event.stage] : null
    switch (event.event) {
      case 'run_started':
        summary.files = event.files ?? []
        break
      case 'stage_started':
        if (!stage) break
        // Earlier stages that never started were turned off
        for (const name of STAGES.slice(0, STAGES.indexOf(stage.name)))
          if (stages[name].state === 'waiting') stages[name].state = 'skipped'
        Object.assign(stage, { state: 'running', done: 0, total: event.total ?? null, startedAt: time(event), lastAt: null })
        summary.current = stage.name
        break
      case 'page_done':
        if (!stage) break
        stage.state = 'running'
        stage.done = event.done ?? stage.done + 1
        stage.total = event.total ?? stage.total
        stage.lastAt = time(event)
        break
      case 'stage_done':
        if (!stage) break
        stage.state = 'done'
        if (stage.total !== null) stage.done = stage.total
        break
      case 'warning':
      case 'error':
        if (event.message)
          summary.notices.push({ kind: event.event, message: event.message.trim(), file: event.file, page: event.page, time: event.time })
        break
      case 'run_finished':
        summary.finished = {
          status: event.status ?? 'failed',
          failed_pages: event.failed_pages ?? 0,
          outputs: event.outputs ?? {},
        }
        break
    }
  }

  for (const stage of Object.values(stages)) {
    if (summary.finished && stage.state === 'running') stage.state = summary.finished.status === 'done' ? 'done' : 'stopped'
    if (summary.finished?.status === 'done' && stage.state === 'waiting') stage.state = 'skipped'
    if (stage.state === 'running' && stage.startedAt !== null && stage.lastAt !== null && stage.total && stage.done >= 2) {
      const perItem = (stage.lastAt - stage.startedAt) / stage.done
      stage.remaining = Math.max(0, Math.round((perItem * (stage.total - stage.done)) / 1000))
    }
  }
  return summary
}

export function plural(count: number, [one, many]: [string, string]): string {
  return `${count} ${count === 1 ? one : many}`
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return 'less than a minute'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return plural(minutes, ['minute', 'minutes'])
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${plural(hours, ['hour', 'hours'])} ${plural(rest, ['minute', 'minutes'])}` : plural(hours, ['hour', 'hours'])
}
