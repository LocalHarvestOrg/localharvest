<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhPlus from '~icons/ph/plus'
import IPhTrash from '~icons/ph/trash'
import { computed, useId } from 'vue'

import type { GuideColumn, GuideExampleRow, GuideRelationship } from '@/api/types'
import { useConfigStore } from '@/stores/config'

import { COLUMN_LABELS, emptyExampleRow, exampleColumns } from './guideFields'
import GuideField from './GuideField.vue'

// The right answer for the example page, as the rows the AI should produce. The backend turns it into the exact
// answer format, so what is written here can never break that format.

const ROWS = 'guide.example_rows'
const MAIN = 'guide.example_main_entity'
const JURISDICTION = 'guide.example_jurisdiction'

const config = useConfigStore()
const id = useId()

const rows = computed(() => (config.get(ROWS) as GuideExampleRow[] | undefined) ?? [])
const on = computed(() => (config.get('guide.columns') as string[] | undefined) ?? [])
const custom = computed(() => (config.get('guide.custom_columns') as GuideColumn[] | undefined) ?? [])
const entity = computed(() => String(config.get('guide.entity') ?? '').trim() || 'entity')
const relationships = computed(() =>
  ((config.get('guide.relationships') as GuideRelationship[] | undefined) ?? [])
    .map((relationship) => relationship.name.trim())
    .filter(Boolean),
)

const columns = computed(() => exampleColumns(on.value, custom.value.length))
const showJurisdiction = computed(() => on.value.includes('jurisdiction'))

function label(key: string): string {
  const index = Number(key.replace('custom_', '')) - 1
  return key.startsWith('custom_') ? custom.value[index]?.name.trim() || 'Your own column' : COLUMN_LABELS[key]
}

/** The relationships to choose from: the ones above, plus the one the cell holds if it is no longer among them. */
function relationshipChoices(current: string | null): string[] {
  return current && !relationships.value.includes(current) ? [...relationships.value, current] : relationships.value
}

const changed = computed(() => [ROWS, MAIN, JURISDICTION].some((path) => !config.isDefault(path)))
const unknownRelationship = computed(() =>
  rows.value.some((row) => row.relationship && !relationships.value.includes(row.relationship)),
)

function setRows(next: GuideExampleRow[]) {
  config.set(ROWS, next)
}

function edit(index: number, key: string, value: string) {
  setRows(rows.value.map((row, i) => (i === index ? { ...row, [key]: value === '' ? null : value } : row)))
}

function reset() {
  for (const path of [ROWS, MAIN, JURISDICTION]) config.resetToDefault(path)
}

function textOf(path: string): string {
  return String(config.get(path) ?? '')
}
</script>

<template>
  <guide-field
    label="What should the AI find on that page?"
    :changed="changed"
    :messages="config.messagesFor(ROWS)"
    @reset="reset"
  >
    <template #help>
      Fill in one row for each related person or company on the example page. These rows are shown to the AI as the
      right answer, so write them exactly as you want them in your results. Leave a cell empty when the page does not
      say.
    </template>

    <div class="row g-2 mb-3">
      <div class="col-md-6">
        <label :for="`${id}-main`" class="form-label small mb-1">Main {{ entity }} on the page</label>
        <input
          :id="`${id}-main`"
          type="text"
          class="form-control form-control-sm"
          :value="textOf(MAIN)"
          @input="config.set(MAIN, ($event.target as HTMLInputElement).value)"
        />
      </div>
      <div v-if="showJurisdiction" class="col-md-6">
        <label :for="`${id}-jurisdiction`" class="form-label small mb-1">Jurisdiction on the page</label>
        <input
          :id="`${id}-jurisdiction`"
          type="text"
          class="form-control form-control-sm"
          :value="textOf(JURISDICTION)"
          @input="config.set(JURISDICTION, ($event.target as HTMLInputElement).value || null)"
        />
      </div>
    </div>

    <!-- position-relative: the hidden "Remove" label is absolutely positioned and would otherwise widen the page -->
    <div class="table-responsive position-relative">
      <table class="table table-sm align-middle mb-2">
        <thead>
          <tr class="small text-secondary">
            <th v-for="key in columns" :key="key" scope="col" class="fw-normal">{{ label(key) }}</th>
            <th scope="col"><span class="visually-hidden">Remove</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="index">
            <td v-for="key in columns" :key="key" class="example-cell" :class="{ 'example-cell--wide': key === 'relation_address' }">
              <select
                v-if="key === 'relationship'"
                class="form-select form-select-sm"
                :class="{ 'is-invalid': row.relationship && !relationships.includes(row.relationship) }"
                :aria-label="`Row ${index + 1}: ${label(key)}`"
                :value="row[key] ?? ''"
                @change="edit(index, key, ($event.target as HTMLSelectElement).value)"
              >
                <option value=""></option>
                <option v-for="name in relationshipChoices(row.relationship)" :key="name" :value="name">{{ name }}</option>
              </select>
              <select
                v-else-if="key === 'relation_type'"
                class="form-select form-select-sm"
                :aria-label="`Row ${index + 1}: ${label(key)}`"
                :value="row[key] ?? ''"
                @change="edit(index, key, ($event.target as HTMLSelectElement).value)"
              >
                <option value=""></option>
                <option value="Person">Person</option>
                <option value="Company">Company</option>
              </select>
              <input
                v-else
                type="text"
                class="form-control form-control-sm"
                :aria-label="`Row ${index + 1}: ${label(key)}`"
                :value="row[key] ?? ''"
                @input="edit(index, key, ($event.target as HTMLInputElement).value)"
              />
            </td>
            <td class="text-end">
              <button
                type="button"
                class="btn btn-outline-secondary btn-sm"
                :aria-label="`Remove row ${index + 1}`"
                title="Remove this row"
                @click="setRows(rows.filter((_, i) => i !== index))"
              >
                <app-icon :name="IPhTrash" size="sm" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-if="unknownRelationship" class="form-text text-danger mb-2" role="alert">
      A row uses a relationship that is not in your list above. Choose one from the list.
    </div>
    <button type="button" class="btn btn-outline-secondary btn-sm" @click="setRows([...rows, emptyExampleRow()])">
      <app-icon :name="IPhPlus" size="sm" class="me-1" />
      Add a row
    </button>
  </guide-field>
</template>

<style scoped>
/* Narrow enough that a table with the default columns fits the card, so the remove buttons stay in view */
.example-cell {
  min-width: 7rem;
}
.example-cell--wide {
  min-width: 11rem;
}
</style>
