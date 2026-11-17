<script setup lang="ts">
import { computed, useId } from 'vue'

import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'
import ResetLink from './ResetLink.vue'

const props = defineProps<{
  path: string
  label: string
  help?: string
  placeholder?: string
  /** Errors reported on another field also belong here (e.g. "server" for "server.url"). */
  errorsFrom?: string
  monospace?: boolean
}>()

const config = useConfigStore()
const id = useId()
const messages = computed(() => config.messagesFor(props.errorsFrom ?? props.path))
const value = computed({
  get: () => String(config.get(props.path) ?? ''),
  set: (text: string) => config.set(props.path, text),
})
</script>

<template>
  <div class="text-field mb-3">
    <label :for="id" class="form-label fw-medium">
      {{ label }}
      <reset-link :path="path" />
    </label>
    <input
      :id="id"
      v-model.trim="value"
      type="text"
      class="form-control"
      :class="{ 'is-invalid': messages.length, 'font-monospace': monospace }"
      :placeholder="placeholder"
      spellcheck="false"
      autocomplete="off"
    />
    <field-messages :messages="messages" />
    <div v-if="help" class="form-text">{{ help }}</div>
  </div>
</template>
