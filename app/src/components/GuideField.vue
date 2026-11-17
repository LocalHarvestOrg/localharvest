<script setup lang="ts">
import { useId } from 'vue'

import FieldMessages from './FieldMessages.vue'

// One question of the guided form: the same header as PromptText (label, "Changed", "Reset to default"),
// the guidance, the answer, and what is wrong with it.

defineProps<{
  label: string
  help?: string
  changed?: boolean
  messages?: string[]
}>()
defineEmits<{ reset: [] }>()

const labelId = useId()
</script>

<template>
  <section class="guide-field mb-4" role="group" :aria-labelledby="labelId">
    <div class="d-flex align-items-center mb-1">
      <span :id="labelId" class="fw-medium">{{ label }}</span>
      <span v-if="changed" class="badge text-bg-warning ms-2">Changed</span>
      <button v-if="changed" type="button" class="btn btn-link btn-sm p-0 ms-auto" @click="$emit('reset')">
        Reset to default
      </button>
    </div>
    <div v-if="help || $slots.help" class="form-text mt-0 mb-2">
      <slot name="help">{{ help }}</slot>
    </div>
    <slot />
    <field-messages :messages="[...new Set(messages ?? [])]" />
  </section>
</template>
