<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhPlus from '~icons/ph/plus'
import IPhTrash from '~icons/ph/trash'
import { computed, useId } from 'vue'

import type { GuideColumn, GuideExampleRow } from '@/api/types'
import { useConfigStore } from '@/stores/config'

import { COLUMN_LABELS, MAX_CUSTOM_COLUMNS, OPTIONAL_COLUMNS, orderedColumns, withoutCustomColumn } from './guideFields'
import GuideField from './GuideField.vue'

// The information to extract for each related person or company: standard columns that can be switched off
// (with their description), and up to three columns of the user's own.

const COLUMNS = 'guide.columns'
const CUSTOM = 'guide.custom_columns'
const EXAMPLE_ROWS = 'guide.example_rows'
const description = (key: string) => `extraction.prompt.field_descriptions.${key}`

const config = useConfigStore()
const id = useId()

const on = computed(() => (config.get(COLUMNS) as string[] | undefined) ?? [])
const custom = computed(() => (config.get(CUSTOM) as GuideColumn[] | undefined) ?? [])



const changed = computed(
  () =>
    !config.isDefault(COLUMNS) ||
    !config.isDefault(CUSTOM) ||
    OPTIONAL_COLUMNS.some((key) => !config.isDefault(description(key))),
)
const messages = computed(() => [...config.messagesFor(COLUMNS), ...config.messagesFor(CUSTOM)])

function switchColumn(key: string, checked: boolean) {
  config.set(COLUMNS, orderedColumns(checked ? [...on.value, key] : on.value.filter((k) => k !== key)))
}

function text(path: string): string {
  return String(config.get(path) ?? '')
}

function addCustom() {
  config.set(CUSTOM, [...custom.value, { name: '', description: '' }])
}

function editCustom(index: number, key: keyof GuideColumn, value: string) {
  config.set(
    CUSTOM,
    custom.value.map((column, i) => (i === index ? { ...column, [key]: value } : column)),
  )
}

function removeCustom(index: number) {
  // The cells of the example follow their columns
  const rows = (config.get(EXAMPLE_ROWS) as GuideExampleRow[] | undefined) ?? []
  config.set(
    EXAMPLE_ROWS,
    rows.map((row) => withoutCustomColumn(row, index)),
  )
  config.set(
    CUSTOM,
    custom.value.filter((_, i) => i !== index),
  )
}

function reset() {
  config.resetToDefault(COLUMNS)
  config.resetToDefault(CUSTOM)
  for (const key of OPTIONAL_COLUMNS) config.resetToDefault(description(key))
}
</script>

<template>
  <guide-field
    label="What information do you want for each related entity (person, company or other)?"
    :changed="changed"
    :messages="messages"
    @reset="reset"
  >
    <template #help>
     The name of the person or company, the relationship and any notes are
      always included. Switch off what your documents do not contain, edit a description to say what to look for, or add
      up to {{ MAX_CUSTOM_COLUMNS }} new fields of your own. Each field becomes a column in your final results. 
      Note: In a description, <span class="font-monospace-sm">{entity}</span>
      stands for your answer about the main entity: keep it, so the AI understands your description.
    </template>

    <div v-for="key in OPTIONAL_COLUMNS" :key="key" class="border rounded p-3 mb-2" :class="{ 'bg-body-tertiary': !on.includes(key) }">
      <div class="form-check form-switch">
        <input
          :id="`${id}-${key}`"
          class="form-check-input"
          type="checkbox"
          role="switch"
          :checked="on.includes(key)"
          @change="switchColumn(key, ($event.target as HTMLInputElement).checked)"
        />
        <label :for="`${id}-${key}`" class="form-check-label">{{ COLUMN_LABELS[key] }}</label>
        <span class="font-monospace-sm text-secondary ms-2">{{ key }}</span>
        <button
          v-if="!config.isDefault(description(key))"
          type="button"
          class="btn btn-link btn-sm p-0 ms-3"
          @click="config.resetToDefault(description(key))"
        >
          Reset description
        </button>
      </div>
      <textarea
        class="form-control form-control-sm mt-2"
        rows="2"
        :disabled="!on.includes(key)"
        :aria-label="`Description of ${COLUMN_LABELS[key]}`"
        :value="text(description(key))"
        @input="config.set(description(key), ($event.target as HTMLTextAreaElement).value)"
      />
      <div
        v-for="message in config.messagesFor(description(key))"
        :key="message"
        class="invalid-feedback d-block"
        role="alert"
      >
        {{ message }}
      </div>
    </div>

    <div v-for="(column, index) in custom" :key="index" class="border rounded p-3 mb-2">
      <div class="row g-2 align-items-start">
        <div class="col-md-4">
          <input
            type="text"
            class="form-control form-control-sm"
            placeholder="Name of the column, for example: Share percentage"
            :aria-label="`Your own column ${index + 1}: name`"
            :value="column.name"
            @input="editCustom(index, 'name', ($event.target as HTMLInputElement).value)"
          />
        </div>
        <div class="col-md-7">
          <textarea
            class="form-control form-control-sm"
            rows="2"
            placeholder="What the AI should write in it, for example: Percentage of shares held, as written in the document"
            :aria-label="`Your own column ${index + 1}: description`"
            :value="column.description"
            @input="editCustom(index, 'description', ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
        <div class="col-md-1">
          <button
            type="button"
            class="btn btn-outline-secondary btn-sm"
            :aria-label="`Remove your own column ${index + 1}`"
            title="Remove this column"
            @click="removeCustom(index)"
          >
            <app-icon :name="IPhTrash" size="sm" />
          </button>
        </div>
      </div>
    </div>

    <button
      type="button"
      class="btn btn-outline-secondary btn-sm mt-1"
      :disabled="custom.length >= MAX_CUSTOM_COLUMNS"
      @click="addCustom"
    >
      <app-icon :name="IPhPlus" size="sm" class="me-1" />
      Add a column of your own
    </button>
    <span v-if="custom.length >= MAX_CUSTOM_COLUMNS" class="form-text ms-2">
      You can add up to {{ MAX_CUSTOM_COLUMNS }}.
    </span>
  </guide-field>
</template>
