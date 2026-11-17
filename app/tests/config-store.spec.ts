import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { SidecarClient } from '@/api/client'
import type { RunConfig, Validation } from '@/api/types'
import { useConfigStore, VALIDATE_DELAY_MS, withoutPlaceholder, withPlaceholder } from '@/stores/config'
import { useSidecarStore } from '@/stores/sidecar'

import schema from './fixtures/schema.json'
import { defaultConfig, fakeTransport, json } from './helpers'

const VALID: Validation = { valid: true, errors: [], config: null, pdfs: ['/docs/a.pdf'], output: { state: 'new', message: 'New.' } }

type Answer = Validation | Promise<Validation>

function setup(answer: (config: RunConfig) => Answer = () => VALID) {
  const transport = fakeTransport(async (path, init) => {
    if (path === '/config/defaults') return json(defaultConfig())
    if (path === '/config/schema') return json(schema)
    if (path === '/config/validate') return json(await answer(JSON.parse(String(init.body))))
    return json({ error: 'not_found', message: path }, 404)
  })
  useSidecarStore().client = new SidecarClient(transport)
  const store = useConfigStore()
  const validations = () => transport.calls.filter((c) => c.path === '/config/validate').map((c) => JSON.parse(String(c.init.body)) as RunConfig)
  return { store, transport, validations }
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('config store', () => {
  it('loads the defaults and the schema, then checks them', async () => {
    const { store, validations } = setup()
    await store.load()
    expect(store.config?.triage.dpi).toBe(150)
    expect(store.limits('triage.dpi')).toMatchObject({ min: 36, max: 600 })
    expect(store.restored).toBe(false)
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(validations()).toHaveLength(1)
    expect(store.validation).toEqual(VALID)
  })

  it('checks edits once typing pauses', async () => {
    const { store, validations } = setup()
    await store.load()
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    store.set('triage.dpi', 72)
    await vi.advanceTimersByTimeAsync(100)
    store.set('triage.dpi', 80)
    expect(store.validating).toBe(true)
    expect(store.canStart).toBe(false)
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(validations().map((c) => c.triage.dpi)).toEqual([150, 80])
    expect(store.validating).toBe(false)
    expect(store.canStart).toBe(true)
  })

  it('drops an answer about settings that changed since', async () => {
    let release: (v: Validation) => void = () => undefined
    let calls = 0
    const { store } = setup(() => {
      calls++
      if (calls === 1) return new Promise<Validation>((resolve) => (release = resolve))
      return { ...VALID, pdfs: ['second'] }
    })
    await store.load()
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS) // first check, held
    store.set('triage.dpi', 90)
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS) // second check answers at once
    release({ ...VALID, pdfs: ['first, out of date'] })
    await vi.advanceTimersByTimeAsync(0)
    expect(store.pdfs).toEqual(['second'])
  })

  it('maps errors to their fields with friendly wording', async () => {
    const { store } = setup(() => ({
      ...VALID,
      valid: false,
      errors: [{ field: 'triage.dpi', loc: ['triage', 'dpi'], message: 'Input should be greater than or equal to 36', type: 'greater_than_equal' }],
    }))
    await store.load()
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(store.messagesFor('triage.dpi')).toEqual(['Use 36 or more.'])
    expect(store.messagesFor('triage')).toHaveLength(1)
    expect(store.messagesFor('extraction')).toEqual([])
    expect(store.canStart).toBe(false)
  })

  it('checks the documents before a results folder is chosen', async () => {
    const { store, validations } = setup((config) => ({
      ...VALID,
      config,
      output: { state: 'new', message: 'New.' },
    }))
    await store.load()
    store.addInputs(['/docs/a.pdf', ' /docs/a.pdf ', ''])
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(store.config?.inputs).toEqual(['/docs/a.pdf'])
    const sent = validations().at(-1)!
    expect(sent.output_dir).toMatch(/^\/docs\/a\.pdf\/__/)
    expect(store.config?.output_dir).toBeNull() // the stand-in never reaches the settings
    expect(store.pdfs).toEqual(['/docs/a.pdf'])
    expect(store.outputState).toBeNull()
    expect(store.canStart).toBe(false)
    expect(store.messagesFor('output_dir')).toEqual(['Choose a folder for the results.'])

    store.setOutputDir(' /results/run-1 ')
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(validations().at(-1)!.output_dir).toBe('/results/run-1')
    expect(store.outputState?.state).toBe('new')
    expect(store.canStart).toBe(true)
  })

  it('resets a setting to its default', async () => {
    const { store } = setup()
    await store.load()
    store.set('extraction.prompt.field_descriptions.NOTES', 'Anything else')
    expect(store.isDefault('extraction.prompt')).toBe(false)
    store.resetToDefault('extraction.prompt.field_descriptions.NOTES')
    expect(store.isDefault('extraction.prompt')).toBe(true)
  })

  it('saves and applies presets without the documents or the folder', async () => {
    const { store } = setup()
    await store.load()
    store.addInputs(['/docs/a.pdf'])
    store.setOutputDir('/results/one')
    store.set('extraction.dpi', 300)
    store.set('triage.prompt.instructions', 'Look for share registers.')
    store.savePreset('Scans')
    const saved = JSON.parse(localStorage.getItem('table-extract.presets')!).settings
    expect(saved.Scans.inputs).toBeUndefined()
    expect(saved.Scans.output_dir).toBeUndefined()

    store.resetSettings()
    expect(store.config?.extraction.dpi).toBe(200)
    expect(store.config?.inputs).toEqual(['/docs/a.pdf'])
    store.setOutputDir('/results/two')
    store.applyPreset('Scans')
    expect(store.config?.extraction.dpi).toBe(300)
    expect(store.config?.triage.prompt.instructions).toBe('Look for share registers.')
    expect(store.config?.output_dir).toBe('/results/two')

    store.deletePreset('Scans')
    expect(store.presets).toEqual({})
  })

  it('keeps the pages chosen in a document with the documents, not in presets', async () => {
    const { store } = setup()
    await store.load()
    store.addInputs(['/docs/a.pdf'])
    store.setChosenPages('a.pdf', '3, 7-12')
    expect(store.config?.triage.pages).toEqual({ 'a.pdf': '3, 7-12' })

    store.set('extraction.dpi', 300)
    store.savePreset('Scans')
    const saved = JSON.parse(localStorage.getItem('table-extract.presets')!).settings
    expect(saved.Scans.triage?.pages).toBeUndefined()
    expect(JSON.stringify(saved)).not.toContain('3, 7-12')

    store.resetSettings()
    expect(store.config?.extraction.dpi).toBe(200)
    expect(store.config?.triage.pages).toEqual({ 'a.pdf': '3, 7-12' })
    store.setChosenPages('a.pdf', '4')
    store.applyPreset('Scans')
    expect(store.config?.extraction.dpi).toBe(300)
    expect(store.config?.triage.pages).toEqual({ 'a.pdf': '4' })

    store.removeChosenPages('a.pdf')
    expect(store.config?.triage.pages).toEqual({})
  })

  it('brings back the settings of the last run after a restart', async () => {
    const first = setup().store
    await first.load()
    first.addInputs(['/docs/a.pdf'])
    first.setOutputDir('/results/one')
    first.set('triage.workers', 1)
    first.rememberRun()

    setActivePinia(createPinia())
    const second = setup().store
    await second.load()
    expect(second.restored).toBe(true)
    expect(second.config?.inputs).toEqual(['/docs/a.pdf'])
    expect(second.config?.triage.workers).toBe(1)
    expect(second.config?.triage.prompt.instructions).toBe(defaultConfig().triage.prompt.instructions)

    second.startOver()
    expect(second.config?.inputs).toEqual([])
    expect(localStorage.getItem('table-extract.last-run')).toBeNull()
  })

  it('drops the template edits saved by version 2, keeping raw prompts and column descriptions', async () => {
    const saved = {
      triage: { workers: 2, prompt: { instructions: 'old template edit', raw: 'kept [marker]' } },
      extraction: { prompt: { rules: 'old rules', field_descriptions: { relation_country: 'Country, as written' } } },
    }
    localStorage.setItem('table-extract.last-run', JSON.stringify({ version: 2, settings: saved }))
    localStorage.setItem('table-extract.presets', JSON.stringify({ version: 2, settings: { mine: saved } }))
    const { store } = setup()
    await store.load()
    const defaults = defaultConfig()
    expect(store.config?.triage.workers).toBe(2)
    expect(store.config?.triage.prompt.instructions).toBe(defaults.triage.prompt.instructions)
    expect(store.config?.triage.prompt.raw).toBe('kept [marker]')
    expect(store.config?.extraction.prompt.rules).toBe(defaults.extraction.prompt.rules)
    expect(store.config?.extraction.prompt.field_descriptions.relation_country).toBe('Country, as written')
    expect(store.presets.mine).toEqual({
      triage: { workers: 2, prompt: { raw: 'kept [marker]' } },
      extraction: { prompt: { field_descriptions: { relation_country: 'Country, as written' } } },
    })
  })

  it('shows the prompts as the answers write them until one is edited by hand', async () => {
    let written = 'Written for a company'
    const { store } = setup(() => ({ ...VALID, prompts: { triage: 'Triage text', extraction: written } }))
    await store.load()
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(store.rawPrompt('extraction')).toBe('Written for a company')
    expect(store.isHandEdited('extraction')).toBe(false)

    store.editRawPrompt('extraction', 'Written for a company, edited')
    expect(store.isHandEdited('extraction')).toBe(true)
    written = 'Written for a person'
    store.set('guide.entity', 'person')
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(store.rawPrompt('extraction')).toBe('Written for a company, edited')
    expect(store.isHandEdited('triage')).toBe(false)

    // Typed back to what the answers write, it follows them again
    store.editRawPrompt('extraction', 'Written for a person')
    expect(store.isHandEdited('extraction')).toBe(false)
    store.editRawPrompt('extraction', 'edited again')
    store.rebuildPrompt('extraction')
    expect(store.rawPrompt('extraction')).toBe('Written for a person')
    expect(store.isDefault('extraction.prompt')).toBe(true)
  })

  it('keeps the last prompts written while an answer has a problem', async () => {
    let prompts: Validation['prompts'] = { triage: 'Triage text', extraction: 'Extraction text' }
    const { store } = setup(() => ({ ...VALID, prompts }))
    await store.load()
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    prompts = { triage: null, extraction: null }
    store.set('guide.entity', '')
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    expect(store.rawPrompt('triage')).toBe('Triage text')
  })

  it('tells whether the model looks for the pages', async () => {
    const { store } = setup(() => ({ ...VALID, pdfs: ['/docs/a.pdf', 'C:\\docs\\b.pdf'] }))
    await store.load()
    expect(store.triageUsed).toBe(true) // documents not found yet
    await vi.advanceTimersByTimeAsync(VALIDATE_DELAY_MS)
    store.setChosenPages('a.pdf', '3')
    expect(store.triageUsed).toBe(true)
    store.setChosenPages('b.pdf', ' ')
    expect(store.triageUsed).toBe(true) // left empty: the model picks the pages
    store.setChosenPages('b.pdf', '1-2')
    expect(store.triageUsed).toBe(false)
    store.removeChosenPages('b.pdf')
    store.set('triage.enabled', false)
    expect(store.triageUsed).toBe(false)
  })
})

describe('placeholder helpers', () => {
  it('use the path style of the first document', () => {
    const config = { ...defaultConfig(), inputs: ['C:\\Docs\\a.pdf'] }
    expect(withPlaceholder(config)?.output_dir).toMatch(/^C:\\Docs\\a\.pdf\\__/)
    expect(withPlaceholder({ ...config, inputs: [] })).toBeNull()
    expect(withPlaceholder({ ...config, output_dir: 'C:\\Out' })).toBeNull()
  })

  it('replace the output errors with "choose a folder"', () => {
    const result = withoutPlaceholder({
      ...VALID,
      errors: [
        { field: 'output_dir', loc: ['output_dir'], message: 'x', type: 'output_dir' },
        { field: 'inputs', loc: ['inputs'], message: 'Not a PDF: /a.txt', type: 'inputs' },
      ],
    })
    expect(result.valid).toBe(false)
    expect(result.output).toBeNull()
    expect(result.errors.map((e) => e.field)).toEqual(['inputs', 'output_dir'])
  })
})
