<script setup lang="ts">
import { computed, useId } from 'vue'

import { friendlyMessage } from '@/api/schema'
import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'

const props = defineProps<{
  path: string
  label: string
  help?: string
  rows?: number
  monospaceLabel?: boolean
  /** A column description: it may not be empty (the sidecar reports that for all columns at once). */
  required?: boolean
}>()

const config = useConfigStore()
const id = useId()

const value = computed({
  get: () => String(config.get(props.path) ?? ''),
  set: (text: string) => config.set(props.path, text),
})
const changed = computed(() => !config.isDefault(props.path))
const empty = computed(() => value.value.trim() === '')
const messages = computed(() => {
  const own = config.errorsFor(props.path).map(friendlyMessage)
  return props.required && empty.value ? [...own, 'Describe this column: it cannot be empty.'] : own
})
</script>

<template>
  <div class="prompt-text mb-3">
    <label :for="id" class="form-label d-flex align-items-center mb-1">
      <slot name="label-icon" />
      <span class="fw-medium" :class="{ 'font-monospace': monospaceLabel }">{{ label }}</span>
      <span v-if="changed" class="badge text-bg-warning ms-2">Changed</span>
      <button
        v-if="changed"
        type="button"
        class="btn btn-link btn-sm p-0 ms-auto"
        @click="config.resetToDefault(path)"
      >
        Reset to default
      </button>
    </label>
    <div v-if="help || $slots.help" class="form-text mt-0 mb-1">
      <slot name="help">{{ help }}</slot>
    </div>
    <textarea
      :id="id"
      v-model="value"
      class="form-control prompt-text__area"
      :class="{ 'is-invalid': messages.length }"
      :rows="rows ?? 6"
      spellcheck="true"
    />
    <field-messages :messages="messages" />
  </div>
</template>

<style scoped>
.prompt-text__area {
  font-family: var(--bs-font-monospace);
  font-size: 0.85rem;
  line-height: 1.45;
}
</style>
