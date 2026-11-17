<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhPlus from '~icons/ph/plus'
import IPhTrash from '~icons/ph/trash'
import { computed, nextTick, ref } from 'vue'

import type { GuideRelationship } from '@/api/types'
import { useConfigStore } from '@/stores/config'

import GuideField from './GuideField.vue'

// The allowed values of the "relationship" column, each with a description of what it covers.

const PATH = 'guide.relationships'

const config = useConfigStore()
const root = ref<HTMLElement | null>(null)

const rows = computed(() => (config.get(PATH) as GuideRelationship[] | undefined) ?? [])
const entity = computed(() => String(config.get('guide.entity') ?? '').trim() || 'entity')

function setRows(next: GuideRelationship[]) {
  config.set(PATH, next)
}

function edit(index: number, key: keyof GuideRelationship, text: string) {
  setRows(rows.value.map((row, i) => (i === index ? { ...row, [key]: text } : row)))
}

async function add() {
  setRows([...rows.value, { name: '', description: '' }])
  await nextTick()
  const names = root.value?.querySelectorAll<HTMLInputElement>('input[data-relationship-name]')
  names?.[names.length - 1]?.focus()
}

function remove(index: number) {
  setRows(rows.value.filter((_, i) => i !== index))
}
</script>

<template>
  <guide-field
    label="What relationships are you interested in?"
    :changed="!config.isDefault(PATH)"
    :messages="config.messagesFor(PATH)"
    @reset="config.resetToDefault(PATH)"
  >
    <template #help>
      A relationship says how another person or company is connected to the main {{ entity }}, for example
      <em>Owner</em>, <em>Director</em> or <em>Secretary</em>. Each name below becomes one possible answer in the
      <span class="font-monospace-sm">relationship</span> column of your results. In the description, say in a few words
      which terms in your documents reference it: for example for <em>Owner</em>, that could be "shareholders and members".
    </template>
    <div ref="root">
      <div class="row g-2 mb-1 d-none d-md-flex small text-secondary">
        <div class="col-md-3">Relationship</div>
        <div class="col-md-8">What it covers</div>
      </div>
      <div v-for="(row, index) in rows" :key="index" class="row g-2 mb-2 align-items-start">
        <div class="col-md-3">
          <input
            type="text"
            class="form-control form-control-sm"
            data-relationship-name
            placeholder="For example: Owner"
            :aria-label="`Relationship ${index + 1}: name`"
            :value="row.name"
            @input="edit(index, 'name', ($event.target as HTMLInputElement).value)"
          />
        </div>
        <div class="col-md-8">
          <textarea
            class="form-control form-control-sm"
            rows="2"
            placeholder="For example: shareholders, members and parent companies"
            :aria-label="`Relationship ${index + 1}: what it covers`"
            :value="row.description"
            @input="edit(index, 'description', ($event.target as HTMLTextAreaElement).value)"
          />
        </div>
        <div class="col-md-1">
          <button
            type="button"
            class="btn btn-outline-secondary btn-sm"
            :disabled="rows.length <= 1"
            :title="rows.length <= 1 ? 'At least one relationship is needed' : 'Remove this relationship'"
            :aria-label="`Remove relationship ${index + 1}`"
            @click="remove(index)"
          >
            <app-icon :name="IPhTrash" size="sm" />
          </button>
        </div>
      </div>
      <button type="button" class="btn btn-outline-secondary btn-sm mt-1" @click="add">
        <app-icon :name="IPhPlus" size="sm" class="me-1" />
        Add a relationship
      </button>
    </div>
  </guide-field>
</template>
