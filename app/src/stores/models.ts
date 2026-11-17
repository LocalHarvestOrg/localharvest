import { defineStore } from 'pinia'
import { computed, ref, shallowRef, watch } from 'vue'

import type {
  AppSettings,
  InstalledModels,
  Job,
  ModelOption,
  Recommendation,
  RuntimeStatus,
  ServersStatus,
  Verdict,
} from '@/api/types'

import { assessCapability } from './capability'
import { mergeDeep, readStorage, useConfigStore, writeStorage } from './config'
import { useHardwareStore } from './hardware'
import { useSidecarStore } from './sidecar'

export const POLL_MS = 1000
const MODE_KEY = 'table-extract.model-mode'
const CHOICE_KEY = 'table-extract.model-choice'

/** "app": the app downloads and starts the model. "own": a server the user started, at an address they type. */
export type ModelMode = 'app' | 'own'

export type ServersState = 'stopped' | 'starting' | 'ready' | 'failed'

export const VERDICT_TEXT: Record<Verdict, { label: string; text: string; tone: string }> = {
  gpu: {
    label: 'Fits this computer',
    text: 'It runs entirely on the graphics processor, the fastest way.',
    tone: 'success',
  },
  partial: {
    label: 'Slower on this computer',
    text: 'It does not fit entirely in graphics memory, so part of it runs on the processor. It works, but more slowly.',
    tone: 'warning',
  },
  cpu: {
    label: 'Slow on this computer',
    text: 'No graphics processor was found, so it runs on the processor. It works, but slowly.',
    tone: 'warning',
  },
  too_big: {
    label: 'Too big for this computer',
    text: 'This computer does not have enough memory for it.',
    tone: 'danger',
  },
  unknown: {
    label: 'Not checked yet',
    text: 'The app can check whether it fits once llama.cpp is installed.',
    tone: 'secondary',
  },
}

const ACTIVE: Job['state'][] = ['queued', 'downloading', 'verifying', 'installing']

export function isActive(job: Job | null | undefined): boolean {
  return !!job && ACTIVE.includes(job.state)
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

/**
 * The model setup in Settings: install llama.cpp, pick and download a model,
 * start it, and point the run settings at it. Downloads and server starts are polled every POLL_MS.
 */
export const useModelsStore = defineStore('models', () => {
  const sidecar = useSidecarStore()
  const config = useConfigStore()
  const hardware = useHardwareStore()

  const settings = shallowRef<AppSettings | null>(null)
  const runtime = shallowRef<RuntimeStatus | null>(null)
  const recommendation = shallowRef<Recommendation | null>(null)
  const installed = shallowRef<InstalledModels | null>(null)
  const jobs = shallowRef<Job[]>([])
  const servers = shallowRef<ServersStatus | null>(null)
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  /** The last action that failed (an install, download or start refused), in words. */
  const actionError = ref<string | null>(null)
  /** The action waiting for the sidecar's answer, to show a spinner on its button. */
  const busy = ref<string | null>(null)
  const mode = ref<ModelMode>(readStorage<ModelMode>(MODE_KEY) === 'own' ? 'own' : 'app')
  /** A curated model id picked under "Details"; null follows the recommendation. */
  const choiceId = ref<string | null>(readStorage<string>(CHOICE_KEY))

  let timer: ReturnType<typeof setTimeout> | null = null
  let waiting: Promise<boolean> | null = null
  /** A run waits for the model to load before it starts. */
  const loadingForRun = ref(false)

  const offline = computed(() => settings.value?.offline ?? false)
  const runtimeInstalled = computed(() => !!runtime.value?.source)
  const options = computed<ModelOption[]>(() => recommendation.value?.options ?? [])
  const recommended = computed(() => recommendation.value?.model ?? null)
  const chosen = computed(() => options.value.find((o) => o.id === choiceId.value) ?? recommended.value)
  /** This computer cannot run a model, or only very slowly: the warning in Settings and on the welcome screen. */
  const capability = computed(() => assessCapability(recommendation.value, runtime.value))

  /** The newest job for a model id or llama.cpp build. */
  function jobFor(target: string | undefined): Job | null {
    if (!target) return null
    return [...jobs.value].reverse().find((j) => j.target === target) ?? null
  }

  const runtimeJob = computed(() => [...jobs.value].reverse().find((j) => j.kind === 'runtime') ?? null)
  const activeJobs = computed(() => jobs.value.filter(isActive))
  /** Something is downloading: the only time the app uses the internet. */
  const online = computed(() => activeJobs.value.length > 0)

  /** The chosen model: what "Download" fetches. */
  const downloads = computed(() => (chosen.value ? [{ model: chosen.value, job: jobFor(chosen.value.model_id) }] : []))
  const needsDownload = computed(() => downloads.value.some((d) => !d.model.installed))
  const downloading = computed(() => downloads.value.some((d) => isActive(d.job)))

  const vision = computed(() => servers.value?.servers.vision ?? null)
  const serversState = computed<ServersState>(() => {
    if (!vision.value || vision.value.state === 'stopped') return 'stopped'
    return vision.value.state
  })
  /** The servers run the chosen model. */
  const runningChosen = computed(() => !!chosen.value && vision.value?.model_id === chosen.value.model_id)
  /** Runs can start: the app's model is up, or the user brings their own server. */
  const ready = computed(() => mode.value === 'own' || (serversState.value === 'ready' && runningChosen.value))

  const needsPolling = computed(() => activeJobs.value.length > 0 || serversState.value === 'starting')

  async function load(): Promise<void> {
    if (loading.value) return
    loading.value = true
    loadError.value = null
    try {
      const api = sidecar.api
      const [s, r, rec, inst, j, srv] = await Promise.all([
        api.settings(),
        api.runtime(),
        api.recommendation(),
        api.installedModels(),
        api.jobs(),
        api.servers(),
      ])
      settings.value = s
      runtime.value = r
      recommendation.value = rec
      installed.value = inst
      jobs.value = j
      servers.value = srv
      applyServerConfig()
      schedulePoll()
    } catch (e) {
      loadError.value = message(e)
    } finally {
      loading.value = false
    }
  }

  async function refreshModels(): Promise<void> {
    const [rec, inst] = await Promise.all([sidecar.api.recommendation(), sidecar.api.installedModels()])
    recommendation.value = rec
    installed.value = inst
  }

  /** Runs an action, recording a refusal in actionError. */
  async function act<T>(name: string, work: () => Promise<T>): Promise<T | null> {
    busy.value = name
    actionError.value = null
    try {
      return await work()
    } catch (e) {
      actionError.value = message(e)
      return null
    } finally {
      busy.value = null
    }
  }

  function addJob(job: Job): void {
    jobs.value = [...jobs.value.filter((j) => j.id !== job.id), job]
  }

  async function setOffline(on: boolean): Promise<void> {
    await act('offline', async () => {
      settings.value = await sidecar.api.setOffline(on)
      // Turning it on cancels downloads
      if (on) jobs.value = await sidecar.api.jobs()
    })
  }

  async function installRuntime(): Promise<void> {
    const job = await act('install', () => sidecar.api.installRuntime())
    if (job) addJob(job)
    schedulePoll()
  }

  /** Downloads the chosen model if it is not already here; a cancelled download resumes. */
  async function download(): Promise<void> {
    await act('download', async () => {
      for (const { model, job } of downloads.value) {
        if (model.installed || isActive(job)) continue
        addJob(await sidecar.api.download({ curated: model.id }))
      }
    })
    schedulePoll()
  }

  async function cancelDownloads(): Promise<void> {
    await act('cancel', async () => {
      for (const { job } of downloads.value) {
        if (job && isActive(job)) addJob(await sidecar.api.cancelJob(job.id))
      }
    })
  }

  async function cancelJob(id: string): Promise<void> {
    const job = await act('cancel', () => sidecar.api.cancelJob(id))
    if (job) addJob(job)
  }

  async function deleteModel(id: string): Promise<void> {
    await act('delete', async () => {
      installed.value = await sidecar.api.deleteModel(id)
      recommendation.value = await sidecar.api.recommendation()
    })
  }

  function choose(id: string | null): void {
    choiceId.value = id === recommended.value?.id ? null : id
    writeStorage(CHOICE_KEY, choiceId.value)
  }

  function setMode(value: ModelMode): void {
    mode.value = value
    writeStorage(MODE_KEY, value === 'own' ? 'own' : null)
    applyServerConfig()
  }

  async function startServers(): Promise<void> {
    const model = chosen.value
    if (!model) return
    const status = await act('start', () => sidecar.api.startServers({ model_id: model.model_id }))
    if (status) servers.value = status
    schedulePoll()
  }

  /**
   * Starts the chosen model if it is off, then waits until it serves (or stops trying):
   * true once it is ready, with the run settings pointed at it. Callers share one wait.
   */
  function ensureStarted(): Promise<boolean> {
    waiting ??= (async () => {
      loadingForRun.value = true
      try {
        if (serversState.value === 'stopped' || serversState.value === 'failed') await startServers()
        if (serversState.value === 'starting') {
          await new Promise<void>((resolve) => {
            const stop = watch(serversState, (state) => {
              if (state === 'starting') return
              stop()
              resolve()
            })
          })
        }
        return serversState.value === 'ready'
      } finally {
        loadingForRun.value = false
        waiting = null
      }
    })()
    return waiting
  }

  async function stopServers(): Promise<void> {
    const status = await act('stop', () => sidecar.api.stopServers())
    if (status) servers.value = status
  }

  /** Points the run settings at the app's servers once they are up. */
  function applyServerConfig(): void {
    const status = servers.value
    if (mode.value !== 'app' || !config.config || !status || serversState.value !== 'ready') return
    const current = JSON.parse(JSON.stringify(config.config)) as typeof config.config
    const merged = mergeDeep(current, status.config)
    if (JSON.stringify(merged) !== JSON.stringify(current)) config.config = merged
  }

  /** One round of polling: job progress, and the servers while they start. */
  async function poll(): Promise<void> {
    const before = new Map(jobs.value.map((j) => [j.id, j]))
    const watchServers = serversState.value === 'starting'
    try {
      if (activeJobs.value.length) jobs.value = await sidecar.api.jobs()
      const finished = jobs.value.filter((j) => isActive(before.get(j.id)) && !isActive(j))
      if (finished.some((j) => j.kind === 'runtime' && j.state === 'done')) {
        // The new llama.cpp sees the GPU: the memory budget and the recommendation change
        runtime.value = await sidecar.api.runtime()
        void hardware.load(true)
      }
      if (finished.some((j) => j.state === 'done')) await refreshModels()
      if (watchServers) {
        servers.value = await sidecar.api.servers()
        applyServerConfig()
      }
    } catch (e) {
      actionError.value = message(e)
    }
  }

  function schedulePoll(): void {
    if (timer !== null || !needsPolling.value) return
    timer = setTimeout(async () => {
      await poll()
      timer = null
      schedulePoll()
    }, POLL_MS)
  }

  function stopPolling(): void {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  return {
    settings,
    runtime,
    recommendation,
    installed,
    jobs,
    servers,
    loading,
    loadError,
    actionError,
    busy,
    mode,
    choiceId,
    offline,
    runtimeInstalled,
    options,
    recommended,
    chosen,
    capability,
    runtimeJob,
    activeJobs,
    online,
    downloads,
    needsDownload,
    downloading,
    vision,
    serversState,
    runningChosen,
    ready,
    jobFor,
    load,
    setOffline,
    installRuntime,
    download,
    cancelDownloads,
    cancelJob,
    deleteModel,
    choose,
    setMode,
    loadingForRun,
    startServers,
    ensureStarted,
    stopServers,
    applyServerConfig,
    poll,
    stopPolling,
  }
})
