import { defineStore } from 'pinia'
import { computed, ref, shallowRef, watch } from 'vue'

import { ApiError } from '@/api/client'
import type { ActiveStatus, OutputState, RunConfig, RunEvent, RunStatus, StageName } from '@/api/types'
import { platform } from '@/platform'

import { useConfigStore } from './config'
import { summarize } from './progress'
import { useSidecarStore } from './sidecar'

const MAX_EVENTS = 5000
/** Waits between reconnection attempts to the event stream, in ms; then it gives up. */
export const RECONNECT_DELAYS = [500, 1000, 2000, 4000, 8000]

export type Connection = 'idle' | 'live' | 'reconnecting' | 'lost'

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener('abort', () => {
      clearTimeout(timer)
      resolve()
    })
  })
}

export const useRunStore = defineStore('run', () => {
  const sidecar = useSidecarStore()
  const configStore = useConfigStore()

  const status = shallowRef<RunStatus>({ state: 'idle' })
  const events = shallowRef<RunEvent[]>([])
  const lastEventId = ref('')
  const connection = ref<Connection>('idle')
  const starting = ref(false)
  const cancelling = ref(false)
  const error = ref<ApiError | null>(null)
  /** The output folder holds a run with different settings: the user must choose. */
  const conflict = ref<OutputState | null>(null)
  /** Which stages the followed run has turned on (from the settings it started with). */
  const enabled = ref<Partial<Record<StageName, boolean>>>({})
  const startedConfig = shallowRef<RunConfig | null>(null)

  let controller: AbortController | null = null

  const active = computed(() => (status.value.state === 'idle' ? null : (status.value as ActiveStatus)))
  const running = computed(() => status.value.state === 'running' || status.value.state === 'cancelling')
  const finished = computed(() => status.value.state === 'finished')
  const result = computed(() => active.value?.result ?? null)
  const summary = computed(() => summarize(events.value, enabled.value))

  // Long runs must not stop because the computer went to sleep
  watch(running, (on) => {
    platform.keepAwake(on).catch(() => undefined)
    // The output folder now holds a run: "new" becomes "resume"
    if (!on) void configStore.validateNow()
  })

  function enabledStages(config: RunConfig | null): Partial<Record<StageName, boolean>> {
    if (!config) return {}
    return { triage: config.triage.enabled, extraction: true }
  }

  function append(batch: RunEvent[]): void {
    const next = events.value.concat(batch)
    events.value = next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next
  }

  async function refresh(): Promise<RunStatus> {
    status.value = await sidecar.api.status()
    return status.value
  }

  function stopFollowing(): void {
    controller?.abort()
    controller = null
    if (connection.value !== 'lost') connection.value = 'idle'
  }

  /** Reads the event stream until its "end" event, reconnecting with Last-Event-ID when it drops. */
  async function follow(): Promise<void> {
    stopFollowing()
    const mine = new AbortController()
    controller = mine
    let failures = 0
    while (!mine.signal.aborted) {
      connection.value = failures ? 'reconnecting' : 'live'
      try {
        let batch: RunEvent[] = []
        let flushAt = 0
        for await (const message of sidecar.api.events({ lastEventId: lastEventId.value, signal: mine.signal })) {
          if (mine.signal.aborted) return
          failures = 0
          connection.value = 'live'
          if (message.id) lastEventId.value = message.id
          let data: unknown
          try {
            data = JSON.parse(message.data)
          } catch {
            continue
          }
          if (message.event === 'end') {
            append(batch)
            status.value = data as RunStatus
            connection.value = 'idle'
            if (controller === mine) controller = null
            return
          }
          batch.push(data as RunEvent)
          // Replays come in bursts: update the screen a few times a second, not per line
          const now = Date.now()
          if (now - flushAt > 100) {
            append(batch)
            batch = []
            flushAt = now
          }
        }
        append(batch)
      } catch (e) {
        if (mine.signal.aborted) return
        if (e instanceof ApiError && e.code === 'no_run') {
          await refresh().catch(() => undefined)
          connection.value = 'idle'
          return
        }
      }
      // The stream ended without "end", or the connection dropped
      if (failures >= RECONNECT_DELAYS.length) {
        connection.value = 'lost'
        return
      }
      connection.value = 'reconnecting'
      await sleep(RECONNECT_DELAYS[failures++], mine.signal)
    }
  }

  function reset(config: RunConfig | null): void {
    events.value = []
    lastEventId.value = '' // ids are byte offsets in the output folder's events.jsonl
    error.value = null
    conflict.value = null
    startedConfig.value = config
    enabled.value = enabledStages(config)
  }

  /** On launch: pick up a run that is still going (after a reload), or show the last one. */
  async function init(): Promise<void> {
    try {
      const current = await refresh()
      if (current.state === 'idle') return
      const config = configStore.config
      reset(config?.output_dir === current.output_dir ? config : null)
      void follow()
    } catch {
      // the Run screen shows the connection problem
    }
  }

  async function start(overwrite = false): Promise<boolean> {
    const config = configStore.config
    if (!config || starting.value || running.value) return false
    starting.value = true
    error.value = null
    conflict.value = null
    try {
      const snapshot = JSON.parse(JSON.stringify(config)) as RunConfig
      const started = await sidecar.api.start(snapshot, overwrite)
      reset(snapshot)
      status.value = started
      configStore.rememberRun()
      void follow()
      return true
    } catch (e) {
      const apiError = e instanceof ApiError ? e : new ApiError(0, 'unknown', String(e))
      if (apiError.code === 'output_conflict') {
        conflict.value = (apiError.body?.output as OutputState) ?? { state: 'conflict', message: apiError.message }
      } else if (apiError.code === 'run_active') {
        await init()
      } else {
        error.value = apiError
        if (apiError.code === 'invalid_config') void configStore.validateNow()
      }
      return false
    } finally {
      starting.value = false
    }
  }

  async function cancel(): Promise<void> {
    if (!running.value || cancelling.value) return
    cancelling.value = true
    try {
      status.value = await sidecar.api.cancel()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'no_active_run') await refresh().catch(() => undefined)
      else error.value = e instanceof ApiError ? e : new ApiError(0, 'unknown', String(e))
    } finally {
      cancelling.value = false
    }
  }

  function dismissConflict(): void {
    conflict.value = null
  }

  return {
    status,
    events,
    lastEventId,
    connection,
    starting,
    cancelling,
    error,
    conflict,
    enabled,
    startedConfig,
    active,
    running,
    finished,
    result,
    summary,
    refresh,
    follow,
    stopFollowing,
    init,
    start,
    cancel,
    dismissConflict,
  }
})
