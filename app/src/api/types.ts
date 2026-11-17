// Shapes of the sidecar's API (table_extract/server). RunConfig mirrors
// table_extract/config.py; the sidecar validates it, so these types only help
// the editor.

export interface ServerConfig {
  url: string
  model: string
  allow_remote: boolean
}

export interface Sampling {
  temperature: number | null
  top_p: number | null
  top_k: number | null
  seed: number | null
}

export interface TriagePrompt {
  instructions: string
  examples: string
  /** The instructions edited by hand (null: written from the answers). Holds the format marker once. */
  raw: string | null
}

export interface TriageConfig {
  enabled: boolean
  dpi: number
  max_tokens: number
  workers: number | null
  server: ServerConfig | null
  prompt: TriagePrompt
  /** Pages chosen by hand, per PDF file name, e.g. { "report.pdf": "3, 7-12" }. */
  pages: Record<string, string>
}

export interface ExtractionPrompt {
  introduction: string
  task: string
  field_descriptions: Record<string, string>
  rules: string
  retry_reminders: string[]
  /** The instructions edited by hand (null: written from the answers). Holds the format marker once. */
  raw: string | null
}

export interface ExtractionConfig {
  dpi: number
  max_tokens_schedule: number[]
  tokens_per_entity_limit: number
  workers: number | null
  server: ServerConfig | null
  prompt: ExtractionPrompt
}

export interface GuideRelationship {
  name: string
  description: string
}

/** A column of the user's own (custom_1 to custom_3 in the answers the model gives). */
export interface GuideColumn {
  name: string
  description: string
}

/** One row of the example: the answer for each column, by column key (relation_name, ..., custom_1). */
export type GuideExampleRow = Record<string, string | null>

/** The answers to the guided questions (table_extract/prompts.py, Guide). They fill both prompts. */
export interface Guide {
  document_type: string
  table_types: string
  entity: string
  relationships: GuideRelationship[]
  /** The standard columns asked for, besides the ones that are always there. */
  columns: string[]
  custom_columns: GuideColumn[]
  example_page: string
  example_main_entity: string
  example_jurisdiction: string | null
  example_rows: GuideExampleRow[]
  exclusions: string
}

export interface RunConfig {
  inputs: string[]
  recursive: boolean
  output_dir: string | null
  server: ServerConfig
  sampling: Sampling
  triage: TriageConfig
  extraction: ExtractionConfig
  guide: Guide
}

/** One problem with the settings; field is a path such as "triage.dpi" or "inputs[0]". */
export interface FieldError {
  field: string
  loc: (string | number)[]
  message: string
  type: string
}

export type OutputStateName = 'new' | 'resume' | 'conflict' | 'invalid'

export interface OutputState {
  state: OutputStateName
  message: string
  previous_status?: string | null
}

export interface Validation {
  valid: boolean
  errors: FieldError[]
  config: RunConfig | null
  pdfs: string[]
  output: OutputState | null
  /** Each prompt's raw text as the answers write it (null when it cannot be written); missing from older sidecars. */
  prompts?: Record<StageName, string | null>
}

export type StageName = 'triage' | 'extraction'
export type RunResult = 'done' | 'failed' | 'cancelled'

export interface Progress {
  files: number | null
  stage: StageName | null
  done: number
  total: number | null
  warnings: number
  errors: number
  last_message: string | null
  last_error: string | null
  finished: { status: RunResult; failed_pages: number; outputs: Record<string, string> } | null
}

export interface IdleStatus {
  state: 'idle'
}

export interface ActiveStatus {
  state: 'running' | 'cancelling' | 'finished'
  result: RunResult | null
  output_dir: string
  pdfs: number
  pid: number
  started: string
  finished: string | null
  exit_code: number | null
  cancel_requested: boolean
  events_offset: number
  progress: Progress
  stderr: string[]
}

export type RunStatus = IdleStatus | ActiveStatus

/** One line of the run's events.jsonl (see table_extract/events.py). */
export interface RunEvent {
  time: string
  event: string
  message?: string
  stage?: StageName
  file?: string
  page?: number
  done?: number
  total?: number
  files?: string[]
  status?: RunResult
  failed_pages?: number
  outputs?: Record<string, string>
  [key: string]: unknown
}

export interface GpuDevice {
  name: string
  description: string
  backend: string
  gpu: boolean
  memory_total_bytes: number
  memory_free_bytes: number
}

export interface Hardware {
  system: {
    os: string
    os_release: string
    machine: string
    cpu_count: number | null
    cpu_physical_cores: number | null
    unified_memory: boolean
  }
  memory: { total_bytes: number; available_bytes: number }
  llama_server: { path: string | null; found: boolean; error: string | null }
  devices: GpuDevice[]
}

export interface Health {
  app: string
  version: string
}

/** A library listed in the About panel. */
export interface Library {
  name: string
  version: string | null
  /** An SPDX expression when the package gives one ("MIT", "Apache-2.0 OR MIT"). */
  license: string | null
}

/** GET /about: what the engine is made of, and what it downloads while running. */
export interface About {
  version: string
  libraries: Library[]
  llama_cpp: { build: string | null; pinned_build: string; license: string }
  models: { label: string; repo: string; license: string | null; license_url: string | null }[]
}

/** A JSON schema, as returned by GET /config/schema (pydantic's model_json_schema). */
export interface JsonSchema {
  type?: string | string[]
  properties?: Record<string, JsonSchema>
  $ref?: string
  $defs?: Record<string, JsonSchema>
  anyOf?: JsonSchema[]
  items?: JsonSchema
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minItems?: number
  default?: unknown
  description?: string
  title?: string
}

// Model management (table_extract/server: storage, runtime, catalog, downloads, supervisor)

export interface AppSettings {
  /** The air-gapped setting: while on, nothing reaches the network. */
  offline: boolean
  data_dir: string
}

export interface RuntimeVariant {
  id: string
  backend: string
  label: string
  download_bytes: number
}

export interface RuntimeStatus {
  pinned_build: string
  installed: { build: string; variant: string; [key: string]: unknown } | null
  llama_server: string | null
  /** Where llama-server comes from; null when there is none. */
  source: 'installed' | 'override' | 'path' | null
  build: string | null
  update_available: boolean
  gpu_vendors: string[]
  recommended_variant: string | null
  variants: RuntimeVariant[]
  job: Job | null
}

/** gpu: fits in graphics memory; partial: part runs on the processor (slower); cpu: no GPU (slow). */
export type Verdict = 'gpu' | 'partial' | 'cpu' | 'too_big' | 'unknown'

export interface MemoryEstimate {
  context: number
  parts: Record<string, number>
  total_bytes: number
  verdict: Verdict
}

/** One curated model (table_extract/server/models.json) with its estimate for this computer. */
export interface ModelOption {
  id: string
  label: string
  description: string
  repo: string
  revision: string
  quant: string
  context: number
  tested: boolean
  model_id: string
  installed: boolean
  download_bytes: number
  estimate: MemoryEstimate
  /** Scores on ICIJ's benchmark documents; null until the model is benchmarked. */
  benchmark: ModelBenchmark | null
}

export interface StageScore {
  precision: number
  recall: number
  f1: number
  documents: number | null
  pages: number | null
  seconds_per_page: number | null
  /** The GPU the speed was measured on. */
  gpu: string | null
}

export interface ModelBenchmark {
  scored: string | null
  /** Pages with tables found. */
  triage?: StageScore
  /** Relationships found, matched on the relation's name. */
  extraction?: StageScore
}

export interface MemoryBudget {
  gpu_bytes: number
  ram_bytes: number
  unified_memory: boolean
  has_gpu: boolean
  gpu_source: 'llama.cpp' | 'estimated' | null
  known: boolean
}

export interface Recommendation {
  model: ModelOption | null
  reason: string
  options: ModelOption[]
  memory: MemoryBudget
}

export interface InstalledModel {
  id: string
  alias: string
  label: string
  repo: string
  quant: string
  /** 'embedding': bge-m3, downloaded by versions that had the page pre-filter. */
  role: 'vision' | 'embedding'
  curated: string | null
  context: number
  complete: boolean
  size: number
  model_path: string
  [key: string]: unknown
}

export interface InstalledModels {
  models: InstalledModel[]
  disk: { models_bytes: number; free_bytes: number; folder: string }
}

export type JobState = 'queued' | 'downloading' | 'verifying' | 'installing' | 'done' | 'failed' | 'cancelled'

/** A download or llama.cpp install. target is the model id, or the llama.cpp build. */
export interface Job {
  id: string
  kind: 'model' | 'runtime'
  target: string
  label: string
  state: JobState
  error: string | null
  bytes_total: number
  bytes_done: number
  bytes_per_second: number | null
  started: number
  finished: number | null
  result: Record<string, unknown> | null
}

export type DownloadRequest = { curated: string } | { repo: string; quant: string; revision?: string }

export interface FitCheck {
  ok: boolean
  error?: string
  verdict?: Verdict | 'needs_newer_runtime'
  [key: string]: unknown
}

export type ServerRole = 'vision'

export interface ModelServer {
  role: ServerRole
  state: 'starting' | 'ready' | 'failed' | 'stopped'
  error: string | null
  model_id: string
  alias: string
  context: number
  url: string
  port: number
  pid: number
  started: number
  ready_at: number | null
  exit_code: number | null
  log: string[]
}

export interface ServersStatus {
  servers: Record<ServerRole, ModelServer | null>
  /** The run settings that point at the server, to merge into the config. */
  config: { server?: { url: string; model: string } }
}

export interface StartServers {
  model_id: string
  context?: number
}
