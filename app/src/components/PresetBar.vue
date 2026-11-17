<script setup lang="ts">
import { ButtonIcon } from '@icij/murmur'
import IPhArrowCounterClockwise from '~icons/ph/arrow-counter-clockwise'
import IPhFloppyDisk from '~icons/ph/floppy-disk'
import { computed, ref } from 'vue'

import { useConfigStore, withoutChosenPages } from '@/stores/config'

// Saved presets: every setting and instruction, without the documents, the pages
// chosen in them and the results folder. Kept in this computer's app storage.

const config = useConfigStore()
const chosen = ref('')
const naming = ref(false)
const name = ref('')

const names = computed(() => Object.keys(config.presets).sort((a, b) => a.localeCompare(b)))
const settingsChanged = computed(() => {
  const current = config.config
  const defaults = config.defaults
  if (!current || !defaults) return false
  const strip = ({ inputs: _i, output_dir: _o, ...rest }: typeof current) => JSON.stringify(withoutChosenPages(rest))
  return strip(current) !== strip(defaults)
})

function apply() {
  if (chosen.value) config.applyPreset(chosen.value)
}

function save() {
  const trimmed = name.value.trim()
  if (!trimmed) return
  config.savePreset(trimmed)
  chosen.value = trimmed
  naming.value = false
  name.value = ''
}

function remove() {
  if (!chosen.value) return
  config.deletePreset(chosen.value)
  chosen.value = ''
}
</script>

<template>
  <div class="preset-bar d-flex flex-wrap align-items-center gap-2">
    <template v-if="names.length">
      <label for="preset" class="small text-secondary">Saved settings</label>
      <select id="preset" v-model="chosen" class="form-select form-select-sm w-auto" @change="apply">
        <option value="" disabled>Choose…</option>
        <option v-for="preset in names" :key="preset" :value="preset">{{ preset }}</option>
      </select>
      <button v-if="chosen" type="button" class="btn btn-link btn-sm text-danger px-1" @click="remove">
        Delete
      </button>
    </template>

    <form v-if="naming" class="d-flex gap-2" @submit.prevent="save">
      <input
        v-model="name"
        type="text"
        class="form-control form-control-sm"
        placeholder="Name, e.g. Scanned annual reports"
        aria-label="Name of the saved settings"
        autofocus
      />
      <button type="submit" class="btn btn-sm btn-action" :disabled="!name.trim()">Save</button>
      <button type="button" class="btn btn-sm btn-link" @click="naming = false">Cancel</button>
    </form>
    <button-icon
      v-else
      variant="outline-secondary"
      size="sm"
      :icon-left="IPhFloppyDisk"
      label="Save these settings…"
      @click="naming = true"
    />
    <button-icon
      v-if="settingsChanged"
      variant="link"
      size="sm"
      class="ms-auto"
      :icon-left="IPhArrowCounterClockwise"
      label="Reset all settings to default"
      @click="config.resetSettings()"
    />
  </div>
</template>
