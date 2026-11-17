<script setup lang="ts">
import { computed, useId } from 'vue'

import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'

const props = defineProps<{ path: string; label: string; help?: string }>()
const config = useConfigStore()
const id = useId()
const messages = computed(() => config.messagesFor(props.path))
const checked = computed({
  get: () => config.get(props.path) === true,
  set: (on: boolean) => config.set(props.path, on),
})
</script>

<template>
  <div class="switch-field mb-3">
    <div class="form-check form-switch">
      <input :id="id" v-model="checked" class="form-check-input" type="checkbox" role="switch" />
      <label :for="id" class="form-check-label">{{ label }}</label>
    </div>
    <div v-if="help" class="form-text ms-5">{{ help }}</div>
    <field-messages :messages="messages" />
  </div>
</template>
