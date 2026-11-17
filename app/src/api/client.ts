// The one place the UI talks to the sidecar. How requests travel (Vite's dev
// proxy in a browser, tauri-plugin-http in the app) is the Transport's job;
// see transports.ts.

import { readSse, type SseMessage } from './sse'
import type {
  About,
  AppSettings,
  DownloadRequest,
  FieldError,
  FitCheck,
  Hardware,
  Health,
  InstalledModels,
  Job,
  JsonSchema,
  Recommendation,
  RunConfig,
  RunStatus,
  RuntimeStatus,
  ServersStatus,
  StartServers,
  Validation,
} from './types'

export interface Transport {
  /** Sends a request to the sidecar; path starts with "/". Adds the token. */
  request(path: string, init?: RequestInit): Promise<Response>
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly errors: FieldError[]
  readonly body: Record<string, unknown> | null

  constructor(status: number, code: string, message: string, errors: FieldError[] = [], body: Record<string, unknown> | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.errors = errors
    this.body = body
  }

  /** The sidecar could not be reached at all (stopped, or not started yet). */
  get unreachable(): boolean {
    return this.status === 0
  }
}

const UNREACHABLE = 'The app’s background service is not responding.'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export class SidecarClient {
  constructor(private readonly transport: Transport) {}

  private async send(path: string, init: RequestInit = {}): Promise<Response> {
    let response: Response
    try {
      response = await this.transport.request(path, init)
    } catch (error) {
      if (init.signal?.aborted) throw error
      throw new ApiError(0, 'unreachable', UNREACHABLE)
    }
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null)
      if (isRecord(body) && typeof body.error === 'string') {
        const errors = Array.isArray(body.errors) ? (body.errors as FieldError[]) : []
        throw new ApiError(response.status, body.error, String(body.message ?? body.error), errors, body)
      }
      throw new ApiError(response.status, 'http_error', `The background service answered ${response.status}.`)
    }
    return response
  }

  private async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.send(path, init)
    return (await response.json()) as T
  }

  private post<T>(path: string, body?: unknown, signal?: AbortSignal, method = 'POST'): Promise<T> {
    return this.json<T>(path, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  }

  health(): Promise<Health> {
    return this.json('/health')
  }

  about(): Promise<About> {
    return this.json('/about')
  }

  defaults(): Promise<RunConfig> {
    return this.json('/config/defaults')
  }

  schema(): Promise<JsonSchema> {
    return this.json('/config/schema')
  }

  validate(config: RunConfig, signal?: AbortSignal): Promise<Validation> {
    return this.post('/config/validate', config, signal)
  }

  /** Starts a run (202). Throws ApiError run_active, output_conflict or invalid_config. */
  start(config: RunConfig, overwrite = false): Promise<RunStatus> {
    return this.post('/run', { config, overwrite })
  }

  status(): Promise<RunStatus> {
    return this.json('/run')
  }

  cancel(): Promise<RunStatus> {
    return this.post('/run/cancel')
  }

  hardware(): Promise<Hardware> {
    return this.json('/hardware')
  }

  settings(): Promise<AppSettings> {
    return this.json('/settings')
  }

  /** Turns the air-gapped setting on or off; turning it on cancels downloads. */
  setOffline(offline: boolean): Promise<AppSettings> {
    return this.post('/settings', { offline }, undefined, 'PUT')
  }

  runtime(): Promise<RuntimeStatus> {
    return this.json('/runtime')
  }

  /** Downloads and installs llama.cpp (a job). Throws ApiError offline or runtime_in_use. */
  installRuntime(variant: string | null = null): Promise<Job> {
    return this.post('/runtime/install', { variant })
  }

  recommendation(): Promise<Recommendation> {
    return this.json('/models/recommendation')
  }

  installedModels(): Promise<InstalledModels> {
    return this.json('/models/installed')
  }

  deleteModel(id: string): Promise<InstalledModels> {
    return this.json(`/models/installed?id=${encodeURIComponent(id)}`, { method: 'DELETE' })
  }

  checkModel(modelId: string): Promise<FitCheck> {
    return this.post('/models/check', { model_id: modelId })
  }

  /** Starts a model download (a job); starting a cancelled one again resumes it. */
  download(request: DownloadRequest): Promise<Job> {
    return this.post('/downloads', request)
  }

  async jobs(): Promise<Job[]> {
    return (await this.json<{ jobs: Job[] }>('/downloads')).jobs
  }

  cancelJob(id: string): Promise<Job> {
    return this.post(`/downloads/${encodeURIComponent(id)}/cancel`)
  }

  servers(): Promise<ServersStatus> {
    return this.json('/servers')
  }

  /** Starts llama-server for the model (202: poll servers()). */
  startServers(request: StartServers): Promise<ServersStatus> {
    return this.post('/servers/start', request)
  }

  stopServers(): Promise<ServersStatus> {
    return this.post('/servers/stop')
  }

  /**
   * The run's events, oldest first, ending with an "end" message holding the
   * final status. Pass the last id seen to continue after a dropped connection.
   */
  async *events(options: { lastEventId?: string; signal?: AbortSignal } = {}): AsyncGenerator<SseMessage> {
    const headers: Record<string, string> = { Accept: 'text/event-stream' }
    if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId
    const response = await this.send('/run/events', { headers, signal: options.signal })
    if (!response.body) throw new ApiError(0, 'unreachable', UNREACHABLE)
    yield* readSse(response.body, options.lastEventId)
  }
}
