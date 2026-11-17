<script setup lang="ts">
import { computed, useId } from 'vue'

import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'
import ResetLink from './ResetLink.vue'

const props = defineProps<{
  path: string
  label: string
  help?: string
  /** Empty means "let the model server decide" (the setting is optional). */
  emptyLabel?: string
  step?: number
  suffix?: string
}>()

const config = useConfigStore()
const id = useId()
const limits = computed(() => config.limits(props.path))
const messages = computed(() => config.messagesFor(props.path))
const fallback = computed(() => config.defaultOf(props.path))

const value = computed({
  get: () => {
    const current = config.get(props.path)
    return current === null || current === undefined ? '' : String(current)
  },
  set: (text: string) => {
    const trimmed = String(text).trim()
    if (trimmed === '') config.set(props.path, null)
    else config.set(props.path, Number.isFinite(Number(trimmed)) ? Number(trimmed) : trimmed)
  },
})

const range = computed(() => {
  const { min, max, exclusiveMin } = limits.value
  if (min !== undefined && max !== undefined) return `From ${exclusiveMin ? `above ${min}` : min} to ${max}.`
  if (min !== undefined && (min > 0 || exclusiveMin)) return exclusiveMin ? `Above ${min}.` : `${min} or more.`
  if (max !== undefined) return `Up to ${max}.`
  return ''
})
</script>

<template>
  <div class="number-field mb-3">
    <label :for="id" class="form-label fw-medium">
      {{ label }}
      <reset-link :path="path" />
    </label>
    <div class="input-group number-field__group">
      <input
        :id="id"
        v-model="value"
        type="number"
        inputmode="decimal"
        class="form-control"
        :class="{ 'is-invalid': messages.length }"
        :min="limits.exclusiveMin ? undefined : limits.min"
        :max="limits.max"
        :step="step ?? (limits.integer ? 1 : 'any')"
        :placeholder="emptyLabel"
        :aria-describedby="`${id}-help`"
      />
      <span v-if="suffix" class="input-group-text">{{ suffix }}</span>
    </div>
    <field-messages :messages="messages" />
    <div :id="`${id}-help`" class="form-text">
      {{ help }}
      <span v-if="range"> {{ range }}</span>
      <span v-if="fallback !== null && fallback !== undefined"> Default: {{ fallback }}.</span>
      <span v-else-if="emptyLabel"> Leave empty: {{ emptyLabel.toLowerCase() }}.</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.number-field__group {
  max-width: 16rem;
}
</style>
