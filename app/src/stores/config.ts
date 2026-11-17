import { defineStore } from 'pinia'
import { computed, ref, shallowRef, watch } from 'vue'

import { errorsUnder, friendlyMessage, getPath, limitsFor, setPath } from '@/api/schema'
import type { FieldError, JsonSchema, RunConfig, StageName, Validation } from '@/api/types'

import { useSidecarStore } from './sidecar'

export const VALIDATE_DELAY_MS = 300
const PRESETS_KEY = 'table-extract.presets'
const LAST_RUN_KEY = 'table-extract.last-run'

/**
 * What is saved in the browser follows the version below. Since version 2 only what the user changed
 * from the defaults is kept, so a default prompt that improves later also reaches saved settings. Anything saved
 * by version 1 kept the prompts in full: they are dropped when read, or they would hide the defaults.
 * Version 3 edits the prompts as raw text (prompt.raw) instead of templates: template edits saved by
 * version 2 are dropped when read, as nothing in the app shows them any more.
 */
const STORAGE_VERSION = 3

type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends unknown[] ? T[K] : T[K] extends object ? DeepPartial<T[K]> : T[K]
}

/** The settings a preset keeps: what differs from the defaults, without the documents, their chosen pages and the results folder. */
export type Preset = DeepPartial<Omit<RunConfig, 'inputs' | 'output_dir'>>

/** The settings without what belongs to the documents: pages are chosen for one file. */
export function withoutChosenPages<T extends Pick<RunConfig, 'triage'>>(settings: T): T {
  return { ...settings, triage: { ...settings.triage, pages: {} } }
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** base with every key of patch applied, recursively; keys patch lacks keep base's value. */
export function mergeDeep<T>(base: T, patch: unknown): T {
  if (!isPlainObject(base) || !isPlainObject(patch)) return (patch === undefined ? base : patch) as T
  const result: Record<string, unknown> = { ...base }
  for (const [key, value] of Object.entries(patch)) {
    result[key] = key in base ? mergeDeep(base[key], value) : value
  }
  return result as T
}

export function readStorage<T>(key: string): T | null {
  try {
    const text = localStorage.getItem(key)
    return text ? (JSON.parse(text) as T) : null
  } catch {
    return null
  }
}

export function writeStorage(key: string, value: unknown): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // storage unavailable: presets just do not persist
  }
}

/** What value changes from base, keeping only those keys; undefined when nothing differs. */
export function onlyChanges(value: unknown, base: unknown): unknown {
  if (isPlainObject(value) && isPlainObject(base)) {
    // A key that was removed is a change too: keep the whole object
    if (Object.keys(base).some((key) => !(key in value))) return value
    const changes: Record<string, unknown> = {}
    for (const [key, item] of Object.entries(value)) {
      const change = onlyChanges(item, base[key])
      if (change !== undefined) changes[key] = change
    }
    return Object.keys(changes).length > 0 ? changes : undefined
  }
  return JSON.stringify(value) === JSON.stringify(base) ? undefined : value
}

/** Settings saved by version 1 without their prompts, which follow the defaults again. */
export function withoutPrompts<T>(settings: T): T {
  if (!isPlainObject(settings)) return settings
  const copy: Record<string, unknown> = { ...settings }
  for (const section of ['triage', 'extraction']) {
    const value = copy[section]
    if (isPlainObject(value)) {
      const { prompt: _prompt, ...rest } = value
      copy[section] = rest
    }
  }
  return copy as T
}

/** The prompt templates the app no longer edits; prompt.raw and the column descriptions stay. */
const TEMPLATES: Record<StageName, string[]> = {
  triage: ['instructions', 'examples'],
  extraction: ['introduction', 'task', 'rules', 'retry_reminders'],
}

/** Settings saved by version 2 without their prompt template edits, which follow the defaults again. */
export function withoutPromptTemplates<T>(settings: T): T {
  if (!isPlainObject(settings)) return settings
  const copy: Record<string, unknown> = { ...settings }
  for (const [stage, keys] of Object.entries(TEMPLATES)) {
    const section = copy[stage]
    if (!isPlainObject(section) || !isPlainObject(section.prompt)) continue
    const prompt = Object.fromEntries(Object.entries(section.prompt).filter(([key]) => !keys.includes(key)))
    copy[stage] = { ...section, prompt }
  }
  return copy as T
}

/** Settings saved by an older version, as the current one reads them. */
function migrate(settings: unknown, version: number): unknown {
  return version < 2 ? withoutPrompts(settings) : withoutPromptTemplates(settings)
}

function readVersioned<T>(key: string, fromOldVersion: (old: unknown, version: number) => T): T | null {
  const stored = readStorage<unknown>(key)
  if (stored === null) return null
  if (isPlainObject(stored) && typeof stored.version === 'number' && 'settings' in stored) {
    return stored.version === STORAGE_VERSION ? (stored.settings as T) : fromOldVersion(stored.settings, stored.version)
  }
  return fromOldVersion(stored, 1) // version 1 saved the settings themselves
}

function writeVersioned(key: string, settings: unknown): void {
  writeStorage(key, { version: STORAGE_VERSION, settings })
}

function readPresets(): Record<string, Preset> {
  return (
    readVersioned<Record<string, Preset>>(PRESETS_KEY, (old, version) =>
      Object.fromEntries(
        Object.entries(old as Record<string, unknown>).map(([name, p]) => [name, migrate(p, version) as Preset]),
      ),
    ) ?? {}
  )
}

const PLACEHOLDER_NAME = '__no_results_folder_chosen_yet__'
const NO_OUTPUT: FieldError = {
  field: 'output_dir',
  loc: ['output_dir'],
  message: 'Choose a folder for the results.',
  type: 'missing',
}

/**
 * The sidecar checks the documents only once the rest of the config is valid,
 * and the results folder is chosen last. Until it is, a folder name that cannot
 * exist (next to the first document) stands in for it, so the Documents screen
 * can already list the PDFs found and the paths that are wrong. Returns null
 * when no stand-in is needed.
 */
export function withPlaceholder(config: RunConfig): RunConfig | null {
  const first = config.inputs[0]
  if (config.output_dir || !first) return null
  const copy = clone(config)
  const separator = first.includes('\\') && !first.includes('/') ? '\\' : '/'
  copy.output_dir = `${first.replace(/[\\/]+$/, '')}${separator}${PLACEHOLDER_NAME}`
  return copy
}

/** The answer about withPlaceholder's config, as if no folder had been chosen. */
export function withoutPlaceholder(result: Validation): Validation {
  const errors = result.errors.filter((e) => e.field !== 'output_dir')
  return { ...result, valid: false, config: null, output: null, errors: [...errors, NO_OUTPUT] }
}

export const useConfigStore = defineStore('config', () => {
  const sidecar = useSidecarStore()

  const defaults = shallowRef<RunConfig | null>(null)
  const schema = shallowRef<JsonSchema | null>(null)
  const config = ref<RunConfig | null>(null)
  const validation = shallowRef<Validation | null>(null)
  const validating = ref(false)
  const validationError = ref<string | null>(null)
  const loadError = ref<string | null>(null)
  const restored = ref(false)
  const presets = ref<Record<string, Preset>>(readPresets())
  /** Each prompt's raw text as the answers write it: the last one the sidecar could write, kept while an answer has a problem. */
  const previews = ref<Record<StageName, string | null>>({ triage: null, extraction: null })

  let timer: ReturnType<typeof setTimeout> | null = null
  let sequence = 0
  let inFlight: AbortController | null = null

  const errors = computed<FieldError[]>(() => validation.value?.errors ?? [])
  const outputState = computed(() => validation.value?.output ?? null)
  const pdfs = computed(() => validation.value?.pdfs ?? [])
  /**
   * Whether the model looks for the pages (triage) in this run: "Read every page" is off and some document has
   * no pages chosen by hand. Before the documents are found, it is only whether triage is on.
   */
  const triageUsed = computed(() => {
    const triage = config.value?.triage
    if (!triage?.enabled) return false
    if (pdfs.value.length === 0) return true
    return pdfs.value.some((path) => !(triage.pages[path.split(/[\\/]/).pop() ?? '']?.trim()))
  })
  /** Valid and checked for exactly the current settings (no edit pending). */
  const canStart = computed(() => !!validation.value?.valid && !validating.value)

  function errorsFor(field: string): FieldError[] {
    return errorsUnder(errors.value, field)
  }

  function messagesFor(field: string): string[] {
    return errorsFor(field).map(friendlyMessage)
  }

  function limits(path: string) {
    return limitsFor(schema.value, path)
  }

  async function load(): Promise<void> {
    loadError.value = null
    try {
      const [loadedDefaults, loadedSchema] = await Promise.all([sidecar.api.defaults(), sidecar.api.schema()])
      defaults.value = loadedDefaults
      schema.value = loadedSchema
      const last = readVersioned<Partial<RunConfig>>(LAST_RUN_KEY, (old, version) => migrate(old, version) as Partial<RunConfig>)
      restored.value = last !== null
      // The last run's settings come back, so a stopped run can be continued after a restart
      config.value = last ? mergeDeep(clone(loadedDefaults), last) : clone(loadedDefaults)
      // The watch below checks it
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function validateNow(): Promise<Validation | null> {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    if (!config.value) return null
    const mine = ++sequence
    inFlight?.abort()
    const controller = new AbortController()
    inFlight = controller
    validating.value = true
    try {
      const standIn = withPlaceholder(config.value)
      const answer = await sidecar.api.validate(standIn ?? clone(config.value), controller.signal)
      const result = standIn ? withoutPlaceholder(answer) : answer
      if (mine !== sequence) return null // a newer edit is being checked
      validation.value = result
      for (const stage of ['triage', 'extraction'] as const) {
        const text = result.prompts?.[stage]
        if (text != null) previews.value[stage] = text
      }
      validationError.value = null
      return result
    } catch (e) {
      if (mine !== sequence) return null
      validationError.value = e instanceof Error ? e.message : String(e)
      return null
    } finally {
      if (mine === sequence) {
        validating.value = false
        inFlight = null
      }
    }
  }

  function scheduleValidation(): void {
    if (timer !== null) clearTimeout(timer)
    sequence++ // an answer still on its way is about older settings
    inFlight?.abort()
    inFlight = null
    validating.value = true
    timer = setTimeout(() => {
      timer = null
      void validateNow()
    }, VALIDATE_DELAY_MS)
  }

  watch(config, () => scheduleValidation(), { deep: true })

  function get(path: string): unknown {
    return getPath(config.value, path)
  }

  function set(path: string, value: unknown): void {
    if (config.value) setPath(config.value, path, value)
  }

  function defaultOf(path: string): unknown {
    return clone(getPath(defaults.value, path))
  }

  function isDefault(path: string): boolean {
    return JSON.stringify(getPath(config.value, path)) === JSON.stringify(getPath(defaults.value, path))
  }

  function resetToDefault(path: string): void {
    set(path, defaultOf(path))
  }

  /** The raw text of a prompt: as edited by hand, or as the answers write it (null until the sidecar has written it). */
  function rawPrompt(stage: StageName): string | null {
    return config.value?.[stage].prompt.raw ?? previews.value[stage]
  }

  function isHandEdited(stage: StageName): boolean {
    return config.value?.[stage].prompt.raw != null
  }

  /** Text typed in a raw prompt. Typed back to what the answers write, it follows the answers again. */
  function editRawPrompt(stage: StageName, text: string): void {
    set(`${stage}.prompt.raw`, text === previews.value[stage] ? null : text)
  }

  /** Drop the hand edits of a prompt: it is written from the answers again. */
  function rebuildPrompt(stage: StageName): void {
    set(`${stage}.prompt.raw`, null)
  }

  function addInputs(paths: string[]): void {
    if (!config.value) return
    const known = new Set(config.value.inputs)
    for (const path of paths.map((p) => p.trim()).filter(Boolean)) {
      if (!known.has(path)) {
        config.value.inputs.push(path)
        known.add(path)
      }
    }
  }

  function removeInput(index: number): void {
    config.value?.inputs.splice(index, 1)
  }

  function setOutputDir(path: string | null): void {
    if (config.value) config.value.output_dir = path?.trim() || null
  }

  /** The pages chosen by hand for one PDF, by file name ("3, 7-12"; empty leaves it to triage). */
  function setChosenPages(name: string, pages: string): void {
    if (config.value) config.value.triage.pages = { ...config.value.triage.pages, [name]: pages }
  }

  function removeChosenPages(name: string): void {
    if (!config.value) return
    const { [name]: _removed, ...rest } = config.value.triage.pages
    config.value.triage.pages = rest
  }

  /** Settings with the current documents, their chosen pages and the results folder. */
  function keepingDocuments(settings: RunConfig): RunConfig {
    const current = config.value!
    return {
      ...settings,
      inputs: current.inputs,
      output_dir: current.output_dir,
      triage: { ...settings.triage, pages: current.triage.pages ?? {} },
    }
  }

  /** Back to the default settings and prompts, keeping the documents, their pages and the results folder. */
  function resetSettings(): void {
    if (!config.value || !defaults.value) return
    config.value = keepingDocuments(clone(defaults.value))
  }

  /** Everything back to defaults, documents and folder included. */
  function startOver(): void {
    if (!defaults.value) return
    config.value = clone(defaults.value)
    restored.value = false
    writeStorage(LAST_RUN_KEY, null)
  }

  /** Called when a run starts with these settings. */
  function rememberRun(): void {
    if (config.value && defaults.value) writeVersioned(LAST_RUN_KEY, onlyChanges(config.value, defaults.value) ?? {})
  }

  /** A run's settings without what belongs to the documents: the same for the current and the default ones. */
  function presetSettings(full: RunConfig) {
    const { inputs: _inputs, output_dir: _output, ...settings } = clone(full)
    return withoutChosenPages(settings)
  }

  function savePreset(name: string): void {
    if (!config.value || !defaults.value) return
    const changes = onlyChanges(presetSettings(config.value), presetSettings(defaults.value)) ?? {}
    presets.value = { ...presets.value, [name]: changes as Preset }
    writeVersioned(PRESETS_KEY, presets.value)
  }

  function applyPreset(name: string): void {
    const preset = presets.value[name]
    if (!preset || !config.value || !defaults.value) return
    config.value = keepingDocuments(mergeDeep(clone(defaults.value), clone(preset)))
  }

  function deletePreset(name: string): void {
    const { [name]: _removed, ...rest } = presets.value
    presets.value = rest
    writeVersioned(PRESETS_KEY, presets.value)
  }

  return {
    defaults,
    schema,
    config,
    validation,
    validating,
    validationError,
    loadError,
    restored,
    presets,
    errors,
    outputState,
    pdfs,
    triageUsed,
    previews,
    canStart,
    errorsFor,
    messagesFor,
    limits,
    load,
    validateNow,
    scheduleValidation,
    get,
    set,
    defaultOf,
    isDefault,
    resetToDefault,
    rawPrompt,
    isHandEdited,
    editRawPrompt,
    rebuildPrompt,
    addInputs,
    removeInput,
    setOutputDir,
    setChosenPages,
    removeChosenPages,
    resetSettings,
    startOver,
    rememberRun,
    savePreset,
    applyPreset,
    deletePreset,
  }
})
