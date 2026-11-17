<script setup lang="ts">
import { computed, useId } from 'vue'

import type { StageName } from '@/api/types'
import { useConfigStore } from '@/stores/config'

import FieldMessages from './FieldMessages.vue'

// One prompt as raw text (table_extract/prompts.py, editable()). It shows what the answers write until it is
// edited by hand; from then on it keeps the edits (prompt.raw) and the answers no longer change it.

const props = defineProps<{ stage: StageName; label: string }>()

const config = useConfigStore()
const id = useId()

const text = computed(() => config.rawPrompt(props.stage))
const edited = computed(() => config.isHandEdited(props.stage))
const messages = computed(() => config.messagesFor(`${props.stage}.prompt.raw`))
</script>

<template>
  <div class="raw-prompt mb-3">
    <label :for="id" class="form-label d-flex align-items-center mb-1">
      <span class="fw-medium">{{ label }}</span>
      <span v-if="edited" class="badge text-bg-warning ms-2">Edited by hand</span>
      <button
        v-if="edited"
        type="button"
        class="btn btn-link btn-sm p-0 ms-auto"
        @click="config.rebuildPrompt(stage)"
      >
        Rebuild from the answers
      </button>
    </label>
    <div class="form-text mt-0 mb-1">
      <template v-if="edited">
        You edited this text, so the answers to the questions no longer change it. “Rebuild from the answers”
        discards your edits.
      </template>
      <template v-else>Written from your answers. It changes when they do, until you edit it here.</template>
    </div>
    <textarea
      v-if="text !== null"
      :id="id"
      :value="text"
      class="form-control raw-prompt__area"
      :class="{ 'is-invalid': messages.length }"
      rows="22"
      spellcheck="true"
      @input="config.editRawPrompt(stage, ($event.target as HTMLTextAreaElement).value)"
    />
    <p v-else :id="id" class="small text-secondary mb-0">
      The instructions appear here once the answers to the questions have no problem.
    </p>
    <field-messages :messages="messages" />
  </div>
</template>

<style scoped>
.raw-prompt__area {
  font-family: var(--bs-font-monospace);
  font-size: 0.85rem;
  line-height: 1.45;
}
</style>
