<script setup lang="ts">
import { AppIcon, ButtonIcon } from '@icij/murmur'
import IPhPencilSimpleLine from '~icons/ph/pencil-simple-line'
import IPhWarning from '~icons/ph/warning'
import { BTab, BTabs } from 'bootstrap-vue-next'
import { computed, ref } from 'vue'

import { APP_NAME } from '@/app'
import { useConfigStore } from '@/stores/config'

import CollapsibleSection from './CollapsibleSection.vue'
import GuidedPrompt from './GuidedPrompt.vue'
import RawPrompt from './RawPrompt.vue'

// The guarded "Advanced" panel (docs/gui-design.md). The questions describe the documents and the backend writes
// both prompts from the answers (the guide, table_extract/prompts.py). Below them, each prompt can also be read
// and edited as raw text; an edited one keeps its edits until it is rebuilt from the answers. The answer format
// is never part of that text (table_extract/schema.py and prompts.py write it), so results stay readable.
// Opening the section shows the warning; the questions need one more click.

const config = useConfigStore()
const section = defineModel<boolean>('open', { default: false })
const open = ref(false)
const raw = ref(false)

const changedCount = computed(
  () => ['guide', 'triage.prompt', 'extraction.prompt'].filter((path) => !config.isDefault(path)).length,
)
const rawErrors = computed(() => [
  ...config.errorsFor('triage.prompt.raw'),
  ...config.errorsFor('extraction.prompt.raw'),
])
const promptErrors = computed(() => [
  ...config.errorsFor('guide'),
  ...config.errorsFor('triage.prompt'),
  ...config.errorsFor('extraction.prompt'),
])

function resetAll() {
  config.resetToDefault('guide')
  config.resetToDefault('triage.prompt')
  config.resetToDefault('extraction.prompt')
}
</script>

<template>
  <collapsible-section
    v-model:open="section"
    class="advanced-prompts"
    title="Edit instructions given to the model"
    featured
    :icon="IPhPencilSimpleLine"
    description="Answer a few questions to read other kinds of tables, or to pull out different information."
    :badge="changedCount ? 'Changed' : undefined"
  >
    <div class="d-flex align-items-start gap-3">
      <app-icon :name="IPhWarning" size="xl" class="text-warning flex-shrink-0 mt-1" />
      <div class="flex-grow-1">
        <p class="small text-secondary mb-2">
          By default, {{ APP_NAME }} is set up to extract data from corporate ownership tables, such as registers of
          shareholders and directors.
        </p>
        <p class="small text-secondary mb-2">
          To use it on other types of documents, or to pull out different information, answer a few questions about
          your documents. Changing the answers changes the results, so try them on a few documents first and adjust if
          needed.
        </p>
        <p class="small text-secondary mb-3">
          Note for advanced users: below the questions, you can also read and edit the raw instructions (the prompts)
          sent to the AI.
        </p>
        <div v-if="!open" class="d-flex gap-2">
          <button type="button" class="btn btn-outline-secondary" @click="open = true">Show the questions</button>
        </div>
        <div v-else class="d-flex gap-2 mb-3">
          <button type="button" class="btn btn-outline-secondary btn-sm" @click="open = false">Hide</button>
          <button v-if="changedCount" type="button" class="btn btn-outline-danger btn-sm" @click="resetAll">
            Reset everything to default
          </button>
        </div>
        <div v-if="promptErrors.length && !open" class="alert alert-danger py-2 mt-3 mb-0">
          Some answers have a problem. Show the questions to see where.
        </div>
      </div>
    </div>

    <div v-if="open" class="pt-3 mt-3 border-top">
      <guided-prompt />
    </div>

    <div v-if="open" class="pt-3 mt-2 border-top">
      <button type="button" class="btn btn-link px-0" :aria-expanded="raw" @click="raw = !raw">
        {{ raw ? 'Hide' : 'Show' }} the raw instructions
      </button>
      <p v-if="!raw" class="small text-secondary mb-0">
        The questions above write these instructions. You can also read and edit them as they are sent to the AI.
      </p>
      <div v-if="rawErrors.length && !raw" class="alert alert-danger py-2 mt-2 mb-0">
        The raw instructions have a problem. Show them to see where.
      </div>
    </div>

    <div v-if="open && raw" class="mt-2">
      <p class="small text-secondary">
        This is the text sent to the AI with each page, written from your answers. The line
        <span class="font-monospace-sm">[The answer format is added here]</span> marks where {{ APP_NAME }} adds the
        description of the answer it expects, from the columns you chose: keep it.
      </p>
      <b-tabs nav-class="nav-underline">
        <b-tab v-if="config.triageUsed" title="Finding the pages">
          <div class="pt-3">
            <raw-prompt stage="triage" label="Raw instructions for finding the pages" />
          </div>
        </b-tab>
        <b-tab title="Reading the tables">
          <div class="pt-3">
            <raw-prompt stage="extraction" label="Raw instructions for reading the tables" />
          </div>
        </b-tab>
      </b-tabs>
    </div>
    <button-icon v-if="open" variant="link" class="px-0 mt-2" label="Hide the questions" @click="open = false" />
  </collapsible-section>
</template>
