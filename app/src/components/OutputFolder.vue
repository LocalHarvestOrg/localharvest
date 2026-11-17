<script setup lang="ts">
import { ButtonIcon } from '@icij/murmur'
import IPhFolderOpen from '~icons/ph/folder-open'
import { computed, onMounted, ref, watch } from 'vue'

import { suggestedOutputDir } from '@/app'
import FieldMessages from '@/components/FieldMessages.vue'
import { platform } from '@/platform'
import { useConfigStore } from '@/stores/config'

const config = useConfigStore()
const documents = ref<string | null>(null)
const typed = ref(config.config?.output_dir ?? '')
const input = ref<HTMLInputElement | null>(null)

onMounted(async () => {
  documents.value = await platform.documentsFolder()
})

watch(
  () => config.config?.output_dir,
  (value) => {
    if ((value ?? '') !== typed.value.trim()) typed.value = value ?? ''
  },
)

const outputDir = computed(() => config.config?.output_dir ?? null)
const messages = computed(() =>
  // "invalid" is also reported as an error: show it once, in the state box
  config.outputState?.state === 'invalid' ? [] : config.messagesFor('output_dir').filter(() => outputDir.value),
)
const suggestion = computed(() => {
  if (!documents.value || !config.config) return null
  const path = suggestedOutputDir(documents.value, config.config.server.model)
  return path === outputDir.value ? null : path
})

async function choose() {
  const picked = await platform.pickOutputFolder(outputDir.value ?? documents.value ?? undefined)
  if (picked) config.setOutputDir(picked)
}

function commit() {
  config.setOutputDir(typed.value)
}

/** Lets the conflict choice send the user here. */
async function chooseAnother() {
  if (platform.hasNativeFiles) await choose()
  else input.value?.focus()
}

defineExpose({ chooseAnother })
</script>

<template>
  <div class="output-folder">
    <label for="output-dir" class="form-label fw-medium">Where to save the results</label>
    <div class="input-group">
      <input
        id="output-dir"
        ref="input"
        v-model="typed"
        type="text"
        class="form-control font-monospace"
        :class="{ 'is-invalid': messages.length }"
        :placeholder="platform.hasNativeFiles ? 'Choose a folder, or type its full path' : '/Users/me/Documents/Results/run-1'"
        spellcheck="false"
        @change="commit"
        @keyup.enter="commit"
      />
      <button-icon
        v-if="platform.hasNativeFiles"
        variant="outline-action"
        :icon-left="IPhFolderOpen"
        label="Choose…"
        @click="choose"
      />
    </div>
    <field-messages :messages="messages" />
    <div class="form-text">
      A folder of its own for this run. It is created if it does not exist.
      <template v-if="!platform.hasNativeFiles">Type a full path and press Enter.</template>
    </div>
    <div v-if="suggestion" class="small mt-2">
      Suggested:
      <button type="button" class="btn btn-link btn-sm p-0 align-baseline font-monospace text-break-all" @click="config.setOutputDir(suggestion)">
        {{ suggestion }}
      </button>
    </div>
  </div>
</template>
