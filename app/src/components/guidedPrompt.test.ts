// Tests of the guided questions, run against the real config store and the real defaults of the sidecar
// (defaults.fixture.json: RunConfig's defaults with the long prompt texts shortened).

import { mount } from '@vue/test-utils'
import type { VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'
import type { Component } from 'vue'

import type { FieldError, Guide, RunConfig, Validation } from '@/api/types'
import { useConfigStore } from '@/stores/config'

import AdvancedPrompts from './AdvancedPrompts.vue'
import ColumnsEditor from './ColumnsEditor.vue'
import defaultsFixture from './defaults.fixture.json'
import ExampleRowsEditor from './ExampleRowsEditor.vue'
import { withoutCustomColumn } from './guideFields'
import RadioOtherField from './RadioOtherField.vue'
import RelationshipsEditor from './RelationshipsEditor.vue'

// The fixture is what the sidecar sends: if the backend's Guide gains or loses a field, this stops compiling
const guideShape: Guide = defaultsFixture.guide
void guideShape

const answer = { errors: [] as FieldError[], pdfs: [] as string[] }
const PROMPTS = { triage: 'Look for registers.\n[The answer format is added here]', extraction: 'Read the registers.\n[The answer format is added here]' }

vi.mock('@/stores/sidecar', () => ({
  useSidecarStore: () => ({
    api: {
      defaults: async () => structuredClone(defaultsFixture),
      schema: async () => ({}),
      validate: async (): Promise<Validation> => ({
        valid: answer.errors.length === 0,
        errors: answer.errors,
        config: null,
        pdfs: answer.pdfs,
        output: null,
        prompts: PROMPTS,
      }),
    },
  }),
}))

async function setup(component: Component, props: Record<string, unknown> = {}) {
  localStorage.clear()
  answer.errors = []
  answer.pdfs = []
  const pinia = createPinia()
  setActivePinia(pinia)
  const config = useConfigStore()
  await config.load()
  const wrapper = mount(component, {
    props,
    attachTo: document.body,
    global: { plugins: [pinia], config: { globalProperties: { APP_NAME: 'The app' } as never } },
  })
  return { config, wrapper }
}

const guide = (config: ReturnType<typeof useConfigStore>) => config.config!.guide
const button = (wrapper: VueWrapper, text: string) => wrapper.findAll('button').find((b) => b.text().includes(text))
const rowValues = (config: ReturnType<typeof useConfigStore>, key: string) =>
  guide(config).example_rows.map((row) => row[key])

const entityField = {
  path: 'guide.entity',
  label: 'What type of entity do the tables describe?',
  options: [{ label: 'Company', value: 'company' }],
  otherLabel: 'Other',
  otherPlaceholder: 'For example: person',
}

describe('RadioOtherField', () => {
  it('selects the ready-made answer by default', async () => {
    const { wrapper } = await setup(RadioOtherField, entityField)
    const radios = wrapper.findAll('input[type=radio]')
    expect((radios[0].element as HTMLInputElement).checked).toBe(true)
    expect((radios[1].element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.text()).not.toContain('Changed')
  })

  it('Other empties the answer and typing fills it', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    await wrapper.findAll('input[type=radio]')[1].setValue(true)
    expect(guide(config).entity).toBe('')
    expect((wrapper.findAll('input[type=radio]')[1].element as HTMLInputElement).checked).toBe(true)
    await wrapper.find('input.other-answer').setValue('person')
    expect(guide(config).entity).toBe('person')
    expect(wrapper.text()).toContain('Changed')
  })

  it('clicking in the box selects Other', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    await wrapper.find('input.other-answer').trigger('focus')
    expect((wrapper.findAll('input[type=radio]')[1].element as HTMLInputElement).checked).toBe(true)
    expect(guide(config).entity).toBe('')
  })

  it('stays on Other when the typed text equals a ready-made answer', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    await wrapper.findAll('input[type=radio]')[1].setValue(true)
    await wrapper.find('input.other-answer').setValue('company')
    expect(guide(config).entity).toBe('company')
    expect((wrapper.findAll('input[type=radio]')[1].element as HTMLInputElement).checked).toBe(true)
  })

  it('brings back what was typed after choosing the ready-made answer', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    await wrapper.findAll('input[type=radio]')[1].setValue(true)
    await wrapper.find('input.other-answer').setValue('person')
    await wrapper.findAll('input[type=radio]')[0].setValue(true)
    expect(guide(config).entity).toBe('company')
    expect((wrapper.find('input.other-answer').element as HTMLInputElement).value).toBe('person')
    await wrapper.findAll('input[type=radio]')[1].setValue(true)
    expect(guide(config).entity).toBe('person')
  })

  it('goes back to the ready-made answer on "Reset to default"', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    await wrapper.findAll('input[type=radio]')[1].setValue(true)
    await wrapper.find('input.other-answer').setValue('person')
    await button(wrapper, 'Reset to default')!.trigger('click')
    expect(guide(config).entity).toBe('company')
    expect((wrapper.findAll('input[type=radio]')[0].element as HTMLInputElement).checked).toBe(true)
  })

  it('follows a change from outside, such as a preset', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    config.set('guide.entity', 'person')
    await wrapper.vm.$nextTick()
    expect((wrapper.findAll('input[type=radio]')[1].element as HTMLInputElement).checked).toBe(true)
    expect((wrapper.find('input.other-answer').element as HTMLInputElement).value).toBe('person')
    config.set('guide.entity', 'company')
    await wrapper.vm.$nextTick()
    expect((wrapper.findAll('input[type=radio]')[0].element as HTMLInputElement).checked).toBe(true)
  })

  it('shows what the sidecar says is wrong, in plain words', async () => {
    const { wrapper, config } = await setup(RadioOtherField, entityField)
    answer.errors = [{ field: 'guide.entity', loc: ['guide', 'entity'], message: 'this answer cannot be empty', type: 'value_error' }]
    await config.validateNow()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Fill this in: it cannot be empty.')
  })
})

describe('RelationshipsEditor', () => {
  const names = (wrapper: VueWrapper) =>
    wrapper.findAll('input[data-relationship-name]').map((input) => (input.element as HTMLInputElement).value)

  it('lists the default relationships', async () => {
    const { wrapper } = await setup(RelationshipsEditor)
    expect(names(wrapper)).toEqual(['Owner', 'Director', 'Secretary', 'Subsidiary'])
  })

  it('adds an empty row and focuses its name', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    await button(wrapper, 'Add a relationship')!.trigger('click')
    await wrapper.vm.$nextTick()
    expect(guide(config).relationships).toHaveLength(5)
    expect(guide(config).relationships[4]).toEqual({ name: '', description: '' })
    const inputs = wrapper.findAll('input[data-relationship-name]')
    expect(document.activeElement).toBe(inputs[4].element)
  })

  it('removes a row', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    await wrapper.find('[aria-label="Remove relationship 2"]').trigger('click')
    expect(guide(config).relationships.map((r) => r.name)).toEqual(['Owner', 'Secretary', 'Subsidiary'])
  })

  it('keeps the last row', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    config.set('guide.relationships', [{ name: 'Donor', description: '' }])
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[aria-label="Remove relationship 1"]').attributes('disabled')).toBeDefined()
  })

  it('is back to the default, not "Changed", once an edit is undone', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    const description = wrapper.find('textarea[aria-label="Relationship 1: what it covers"]')
    const original = (description.element as HTMLTextAreaElement).value
    await description.setValue('something else')
    expect(wrapper.text()).toContain('Changed')
    await description.setValue(original)
    expect(wrapper.text()).not.toContain('Changed')
    expect(config.isDefault('guide.relationships')).toBe(true)
  })

  it('resets to the default', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    await button(wrapper, 'Add a relationship')!.trigger('click')
    await button(wrapper, 'Reset to default')!.trigger('click')
    expect(guide(config).relationships).toHaveLength(4)
  })

  it('shows the messages for the relationships, without repeating them', async () => {
    const { wrapper, config } = await setup(RelationshipsEditor)
    const error = { loc: ['guide', 'relationships'], type: 'value_error' }
    answer.errors = [
      { ...error, field: 'guide.relationships', message: 'each relationship needs a different name' },
      { ...error, field: 'guide.relationships', message: 'each relationship needs a different name' },
    ]
    await config.validateNow()
    await wrapper.vm.$nextTick()
    expect(wrapper.findAll('.field-messages div')).toHaveLength(1)
    expect(wrapper.text()).toContain('Each relationship needs a different name.')
  })
})

describe('ColumnsEditor', () => {
  const field = (key: string) => `extraction.prompt.field_descriptions.${key}`
  const switches = (wrapper: VueWrapper) => wrapper.findAll('input[role=switch]')

  it('has every standard column on by default', async () => {
    const { wrapper } = await setup(ColumnsEditor)
    expect(switches(wrapper)).toHaveLength(5)
    expect(switches(wrapper).every((s) => (s.element as HTMLInputElement).checked)).toBe(true)
  })

  it('switching a column off leaves it out, and on again restores the default order', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    await switches(wrapper)[2].setValue(false) // relation_country
    await switches(wrapper)[0].setValue(false) // jurisdiction
    expect(guide(config).columns).toEqual(['relation_address', 'relation_nationality', 'relation_type'])
    expect(wrapper.text()).toContain('Changed')
    await switches(wrapper)[0].setValue(true)
    await switches(wrapper)[2].setValue(true)
    expect(guide(config).columns).toEqual(['jurisdiction', 'relation_address', 'relation_country', 'relation_nationality', 'relation_type'])
    expect(config.isDefault('guide.columns')).toBe(true)
    expect(wrapper.text()).not.toContain('Changed')
  })

  it('does not let a column that is off be edited', async () => {
    const { wrapper } = await setup(ColumnsEditor)
    await switches(wrapper)[1].setValue(false)
    expect(wrapper.find('textarea[aria-label="Description of Address"]').attributes('disabled')).toBeDefined()
  })

  it('edits a description and resets just that one', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    const original = config.get(field('relation_country'))
    await wrapper.find('textarea[aria-label="Description of Country"]').setValue('The country, in capitals')
    expect(config.get(field('relation_country'))).toBe('The country, in capitals')
    await button(wrapper, 'Reset description')!.trigger('click')
    expect(config.get(field('relation_country'))).toBe(original)
    expect(button(wrapper, 'Reset description')).toBeUndefined()
  })

  it('shows {entity} as it is in the descriptions, since the sidecar fills it in, and says what it stands for', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    const text = (wrapper.find('textarea[aria-label="Description of Address"]').element as HTMLTextAreaElement).value
    expect(text).toContain('{entity}')
    expect(config.config!.extraction.prompt.field_descriptions.relation_address).toContain('{entity}')
    expect(wrapper.find('.font-monospace-sm').text()).toBe('{entity}')
  })

  it('adds up to three columns of the user\'s own', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    for (let i = 0; i < 3; i++) await button(wrapper, 'Add a column of your own')!.trigger('click')
    expect(guide(config).custom_columns).toHaveLength(3)
    expect(button(wrapper, 'Add a column of your own')!.attributes('disabled')).toBeDefined()
    await wrapper.find('input[aria-label="Your own column 1: name"]').setValue('Share percentage')
    await wrapper.find('textarea[aria-label="Your own column 1: description"]').setValue('Percentage of shares')
    expect(guide(config).custom_columns[0]).toEqual({ name: 'Share percentage', description: 'Percentage of shares' })
  })

  it('removing a column of the user\'s own moves the cells of the example with their columns', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    config.set('guide.custom_columns', [
      { name: 'A', description: '' },
      { name: 'B', description: '' },
      { name: 'C', description: '' },
    ])
    config.set('guide.example_rows', [{ relation_name: 'Alice', custom_1: 'a1', custom_2: 'b1', custom_3: 'c1' }])
    await wrapper.vm.$nextTick()
    await wrapper.find('[aria-label="Remove your own column 1"]').trigger('click')
    expect(guide(config).custom_columns.map((c) => c.name)).toEqual(['B', 'C'])
    expect(guide(config).example_rows[0]).toEqual({ relation_name: 'Alice', custom_1: 'b1', custom_2: 'c1' })
  })

  it('resets the columns, the columns of the user\'s own and the descriptions together', async () => {
    const { wrapper, config } = await setup(ColumnsEditor)
    await switches(wrapper)[0].setValue(false)
    await button(wrapper, 'Add a column of your own')!.trigger('click')
    await wrapper.find('textarea[aria-label="Description of Country"]').setValue('x')
    await wrapper.findAll('button').find((b) => b.text() === 'Reset to default')!.trigger('click')
    expect(config.isDefault('guide.columns')).toBe(true)
    expect(config.isDefault('guide.custom_columns')).toBe(true)
    expect(config.isDefault(field('relation_country'))).toBe(true)
  })

  it('moves example cells up when a column of the user\'s own is removed (helper)', () => {
    expect(withoutCustomColumn({ custom_1: 'a', custom_2: 'b', custom_3: 'c' }, 1)).toEqual({ custom_1: 'a', custom_2: 'c' })
    expect(withoutCustomColumn({ custom_1: 'a' }, 0)).toEqual({})
    expect(withoutCustomColumn({ relation_name: 'x' }, 0)).toEqual({ relation_name: 'x' })
  })
})

describe('ExampleRowsEditor', () => {
  const headers = (wrapper: VueWrapper) => wrapper.findAll('thead th').map((th) => th.text())

  it('has a column for each default column, and for no others', async () => {
    const { wrapper } = await setup(ExampleRowsEditor)
    expect(headers(wrapper)).toEqual(['Name', 'Address', 'Country', 'Nationality', 'Person or company', 'Relationship', 'Remove'])
    expect(wrapper.findAll('tbody tr')).toHaveLength(3)
  })

  it('follows the columns that are on, and the columns of the user\'s own', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    config.set('guide.columns', ['relation_country'])
    config.set('guide.custom_columns', [{ name: 'Share percentage', description: '' }, { name: '', description: '' }])
    await wrapper.vm.$nextTick()
    expect(headers(wrapper)).toEqual(['Name', 'Country', 'Relationship', 'Share percentage', 'Your own column', 'Remove'])
  })

  it('stores an emptied cell as null, so an undone edit is the default again', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    const cell = wrapper.find('input[aria-label="Row 1: Country"]')
    await cell.setValue('UK')
    expect(rowValues(config, 'relation_country')[0]).toBe('UK')
    expect(wrapper.text()).toContain('Changed')
    await cell.setValue('USA')
    expect(config.isDefault('guide.example_rows')).toBe(true)
    await wrapper.find('input[aria-label="Row 3: Country"]').setValue('Spain')
    await wrapper.find('input[aria-label="Row 3: Country"]').setValue('')
    expect(rowValues(config, 'relation_country')[2]).toBeNull()
    expect(config.isDefault('guide.example_rows')).toBe(true)
  })

  it('offers the relationships of the question above, and warns about one that is gone', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    const select = wrapper.find('select[aria-label="Row 1: Relationship"]')
    expect(select.findAll('option').map((o) => o.text())).toEqual(['', 'Owner', 'Director', 'Secretary', 'Subsidiary'])
    expect((select.element as HTMLSelectElement).value).toBe('Owner')
    config.set('guide.relationships', [{ name: 'Shareholder', description: '' }])
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('not in your list above')
    expect(wrapper.find('select[aria-label="Row 1: Relationship"]').classes()).toContain('is-invalid')
    await wrapper.find('select[aria-label="Row 1: Relationship"]').setValue('Shareholder')
    expect(rowValues(config, 'relationship')[0]).toBe('Shareholder')
  })

  it('adds and removes rows', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    await button(wrapper, 'Add a row')!.trigger('click')
    expect(guide(config).example_rows).toHaveLength(4)
    expect(Object.values(guide(config).example_rows[3]).every((v) => v === null)).toBe(true)
    await wrapper.find('[aria-label="Remove row 1"]').trigger('click')
    expect(rowValues(config, 'relation_name')).toEqual(['Bob', 'Acme Holdings', null])
  })

  it('asks for the jurisdiction only when that column is on', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    expect(wrapper.text()).toContain('Jurisdiction on the page')
    await wrapper.findAll('input[type=text]')[1].setValue('Delaware')
    expect(guide(config).example_jurisdiction).toBe('Delaware')
    await wrapper.findAll('input[type=text]')[1].setValue('')
    expect(guide(config).example_jurisdiction).toBeNull()
    config.set('guide.columns', ['relation_country'])
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).not.toContain('Jurisdiction on the page')
  })

  it('names the entity in its labels', async () => {
    const { wrapper, config } = await setup(ExampleRowsEditor)
    expect(wrapper.text()).toContain('Main company on the page')
    config.set('guide.entity', 'person')
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Main person on the page')
    await wrapper.find('input[type=text]').setValue('Jane Roe')
    expect(guide(config).example_main_entity).toBe('Jane Roe')
  })
})

describe('AdvancedPrompts', () => {
  // The panel sits in a collapsible Settings section: open it, as SettingsStep does when the user clicks its title
  const open = { open: true }
  const showQuestions = (wrapper: VueWrapper) => button(wrapper, 'Show the questions')!.trigger('click')

  it('shows nothing of the questions until the warning is accepted', async () => {
    const { wrapper } = await setup(AdvancedPrompts, open)
    expect(button(wrapper, 'Show the questions')).toBeDefined()
    expect(wrapper.text()).not.toContain('What type of documents do you have?')
    await showQuestions(wrapper)
    expect(wrapper.text()).toContain('What type of documents do you have?')
    expect(wrapper.text()).toContain('What relationships are you interested in?')
    expect(wrapper.text()).toContain('What information do you want for each related')
    expect(wrapper.text()).toContain('Describe an example page')
    expect(wrapper.text()).toContain('What should the AI find on that page?')
  })

  // Checks the settings now, once the store has seen the last edit (or its own check would drop this answer)
  const check = async (wrapper: VueWrapper, config: ReturnType<typeof useConfigStore>) => {
    await wrapper.vm.$nextTick()
    await config.validateNow()
    await wrapper.vm.$nextTick()
  }
  const showRaw = async (wrapper: VueWrapper, config: ReturnType<typeof useConfigStore>) => {
    await check(wrapper, config)
    await button(wrapper, 'Show the raw instructions')!.trigger('click')
  }
  const rawText = (wrapper: VueWrapper) =>
    wrapper.findAll('textarea.raw-prompt__area').map((t) => (t.element as HTMLTextAreaElement).value)

  it('keeps the raw instructions behind a second button', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    await showQuestions(wrapper)
    expect(wrapper.text()).not.toContain('Raw instructions for reading the tables')
    await showRaw(wrapper, config)
    expect(wrapper.text()).toContain('Finding the pages')
    expect(wrapper.text()).toContain('Raw instructions for reading the tables')
    expect(wrapper.text()).not.toContain('Reminders when a page is read again')
    expect(rawText(wrapper)).toContain(PROMPTS.extraction)
  })

  it('stops following the answers once the raw text is edited, until it is rebuilt', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    await showQuestions(wrapper)
    await showRaw(wrapper, config)
    const area = wrapper.find('textarea.raw-prompt__area')
    await area.setValue('Edited.\n[The answer format is added here]')
    expect(config.isHandEdited('triage')).toBe(true)
    expect(wrapper.text()).toContain('Edited by hand')
    expect(wrapper.text()).toContain('You edited the raw instructions for finding the pages by hand')
    expect(wrapper.find('h2 .badge').exists()).toBe(true)
    await button(wrapper, 'Rebuild from the answers')!.trigger('click')
    expect(config.isHandEdited('triage')).toBe(false)
    expect(rawText(wrapper)).toContain(PROMPTS.triage)
    expect(wrapper.text()).not.toContain('Edited by hand')
  })

  it('leaves out what finds the pages when the model does not look for them', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    await showQuestions(wrapper)
    expect(wrapper.text()).toContain('Finding the pages')
    expect(wrapper.text()).toContain('Which similar pages should be ignored?')
    config.set('triage.enabled', false)
    await showRaw(wrapper, config)
    expect(wrapper.text()).not.toContain('What type of documents do you have?')
    expect(wrapper.text()).not.toContain('Which similar pages should be ignored?')
    expect(wrapper.text()).not.toContain('Raw instructions for finding the pages')
    expect(wrapper.text()).toContain('What type of tables do you want to extract?')
    expect(wrapper.text()).toContain('Raw instructions for reading the tables')

    // Pages chosen by hand for every document: the same
    config.set('triage.enabled', true)
    answer.pdfs = ['/docs/a.pdf']
    config.setChosenPages('a.pdf', '2-4')
    await check(wrapper, config)
    expect(wrapper.text()).not.toContain('Which similar pages should be ignored?')
  })

  it('marks the card as changed and resets the answers with everything else', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    await showQuestions(wrapper)
    expect(wrapper.find('h2 .badge').exists()).toBe(false)
    config.set('guide.entity', 'person')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('h2 .badge').exists()).toBe(true)
    await button(wrapper, 'Reset everything to default')!.trigger('click')
    expect(guide(config).entity).toBe('company')
    expect(config.isDefault('guide')).toBe(true)
    expect(wrapper.find('h2 .badge').exists()).toBe(false)
  })

  it('points to the questions when an answer has a problem and the card is closed', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    answer.errors = [{ field: 'guide.relationships', loc: ['guide', 'relationships'], message: 'add at least one relationship', type: 'value_error' }]
    await config.validateNow()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('Some answers have a problem')
    await showQuestions(wrapper)
    expect(wrapper.text()).toContain('Add at least one relationship.')
  })

  it('points to the raw instructions when only they have a problem', async () => {
    const { wrapper, config } = await setup(AdvancedPrompts, open)
    await showQuestions(wrapper)
    const message = 'keep the line [The answer format is added here] exactly once: it marks where the answer format goes'
    answer.errors = [{ field: 'extraction.prompt.raw', loc: ['extraction', 'prompt', 'raw'], message, type: 'value_error' }]
    await config.validateNow()
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('The raw instructions have a problem')
    await button(wrapper, 'Show the raw instructions')!.trigger('click')
    await button(wrapper, 'Reading the tables')!.trigger('click')
    expect(wrapper.text()).toContain('Keep the line [The answer format is added here] once')
  })
})

// Keep the type honest: the fixture is a whole RunConfig
const _whole: RunConfig = defaultsFixture as unknown as RunConfig
void _whole
