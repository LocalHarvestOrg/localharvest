<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'

import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'
import ResetLink from './ResetLink.vue'

// A list of whole numbers typed as "4096, 8192, 12288"
const props = defineProps<{ path: string; label: string; help?: string }>()
const config = useConfigStore()
const id = useId()
const messages = computed(() => config.messagesFor(props.path))

function format(value: unknown): string {
  return Array.isArray(value) ? value.join(', ') : ''
}

const text = ref(format(config.get(props.path)))
watch(
  () => config.get(props.path),
  (value) => {
    if (JSON.stringify(parse(text.value)) !== JSON.stringify(value)) text.value = format(value)
  },
  { deep: true },
)

function parse(input: string): (number | string)[] {
  return input
    .split(/[\s,;]+/)
    .filter(Boolean)
    .map((part) => (Number.isFinite(Number(part)) ? Number(part) : part))
}

function update(input: string): void {
  text.value = input
  config.set(props.path, parse(input))
}
</script>

<template>
  <div class="budget-list-field mb-3">
    <label :for="id" class="form-label fw-medium">
      {{ label }}
      <reset-link :path="path" />
    </label>
    <input
      :id="id"
      :value="text"
      type="text"
      class="form-control budget-list-field__input"
      :class="{ 'is-invalid': messages.length }"
      spellcheck="false"
      @input="update(($event.target as HTMLInputElement).value)"
    />
    <field-messages :messages="messages" />
    <div v-if="help" class="form-text">{{ help }} Default: {{ format(config.defaultOf(path)) }}.</div>
  </div>
</template>

<style scoped>
.budget-list-field__input {
  max-width: 24rem;
}
</style>
