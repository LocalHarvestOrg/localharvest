<script setup lang="ts">
import { ButtonIcon } from '@icij/murmur'
import IPhHardDrives from '~icons/ph/hard-drives'
import { computed, onActivated, reactive, watch } from 'vue'

import { friendlyMessage } from '@/api/schema'
import AdvancedPrompts from '@/components/AdvancedPrompts.vue'
import BudgetListField from '@/components/BudgetListField.vue'
import CollapsibleSection from '@/components/CollapsibleSection.vue'
import ComputerFacts from '@/components/ComputerFacts.vue'
import ModelList from '@/components/ModelList.vue'
import ModelSetup from '@/components/ModelSetup.vue'
import NumberField from '@/components/NumberField.vue'
import PresetBar from '@/components/PresetBar.vue'
import StepFooter from '@/components/StepFooter.vue'
import StepHeader from '@/components/StepHeader.vue'
import { useConfigStore } from '@/stores/config'
import { useHardwareStore } from '@/stores/hardware'
import { useModelsStore } from '@/stores/models'

const config = useConfigStore()
const hardware = useHardwareStore()
const models = useModelsStore()

onActivated(() => {
  void hardware.load()
  void models.load()
})

const triageOn = computed(() => !!config.config?.triage.enabled)

// The settings in each closed section, to open it when one has a problem and to mark it "Changed"
const TRIAGE_FIELDS = ['triage.dpi', 'triage.workers', 'triage.max_tokens']
const FIELDS = {
  models: [],
  quality: [...TRIAGE_FIELDS, 'extraction.dpi', 'extraction.workers', 'extraction.max_tokens_schedule',
    'extraction.tokens_per_entity_limit'],
  sampling: ['sampling'],
  prompts: ['guide', 'triage.prompt', 'extraction.prompt'],
  computer: [],
  own: [],
} satisfies Record<string, string[]>
type Section = keyof typeof FIELDS
const open = reactive<Record<Section, boolean>>({
  models: false,
  quality: false,
  sampling: false,
  prompts: false,
  computer: false,
  own: false,
})
const isUnder = (field: string, prefix: string) =>
  field === prefix || field.startsWith(`${prefix}.`) || field.startsWith(`${prefix}[`)

// Open a section when one of its settings has a problem
watch(
  () => config.errors,
  (errors) => {
    for (const section of Object.keys(FIELDS) as Section[]) {
      const fields: string[] = FIELDS[section]
      if (errors.some((e) => fields.some((f) => isUnder(e.field, f)))) open[section] = true
    }
  },
  { immediate: true },
)

const changed = (section: Section) => {
  const fields: string[] = FIELDS[section]
  return fields.some((f) => !config.isDefault(f)) ? 'Changed' : undefined
}

// Fields shown on this screen (or another one) right now; errors elsewhere are listed at the top
const placed = computed(() => {
  const fields = ['inputs', 'output_dir', 'recursive', 'server', 'triage.enabled', 'triage.pages']
  if (open.quality) fields.push(...FIELDS.quality.filter((f) => triageOn.value || !TRIAGE_FIELDS.includes(f)))
  if (open.sampling) fields.push(...FIELDS.sampling)
  if (open.prompts) fields.push(...FIELDS.prompts)
  return fields
})
const unplaced = computed(() => config.errors.filter((e) => !placed.value.some((f) => isUnder(e.field, f))))
</script>

<template>
  <div class="settings-step">
    <step-header
      title="Settings"
      lead="The model that reads the pages, and how it reads them. Everything happens on this computer. Below, you can also set it up for other types of documents, or to pull out different information."
    />

    <model-setup />

    <div class="settings-step__defaults d-flex flex-wrap align-items-center gap-3 mb-4">
      <p class="mb-0 flex-grow-1">
        The defaults work for most documents. Change them below if pages are missed, or to make runs faster.
      </p>
      <preset-bar />
    </div>

    <div v-for="error in unplaced" :key="error.field + error.message" class="alert alert-danger">
      <span v-if="error.field" class="font-monospace small me-2">{{ error.field }}</span>{{ friendlyMessage(error) }}
    </div>

    <advanced-prompts v-model:open="open.prompts" />

    <collapsible-section
      v-if="models.mode === 'app' && models.options.length"
      v-model:open="open.models"
      title="Choose another model"
      description="Other models the app knows work, how fast each would run here, and the ones downloaded."
    >
      <model-list />
    </collapsible-section>

    <collapsible-section
      v-model:open="open.quality"
      title="Reading quality and speed"
      description="Image sharpness, pages read at the same time, and how long the model’s answers may be."
      :badge="changed('quality')"
    >
      <h3 class="h6 mb-1">Finding the pages with tables</h3>
      <p v-if="!triageOn" class="small text-secondary mb-4">
        Not used: “Read every page” is chosen in Documents.
      </p>
      <template v-else>
        <p class="small text-secondary mb-3">
          The model looks at every page and keeps the ones that include tables and list owners, directors or subsidiaries.
        </p>
        <div class="row">
          <div class="col-md-6">
            <number-field
              path="triage.dpi"
              label="Image quality"
              help="Sharper images help with small print, but use more memory and time."
              suffix="dpi"
            />
          </div>
          <div class="col-md-6">
            <number-field
              path="triage.workers"
              label="Pages sent to the model at the same time"
              help="Automatic sends as many as the model server can work on at once. More than that only makes pages wait."
              empty-label="Automatic"
              suffix="at once"
            />
          </div>
          <div class="col-md-6">
            <number-field
              path="triage.max_tokens"
              label="Length of the model’s answer"
              help="A limit on how much the model may write per page."
              suffix="tokens"
            />
          </div>
        </div>
      </template>

      <h3 class="h6 mb-1 mt-2">Reading the tables</h3>
      <p class="small text-secondary mb-3">
        The model reads each page kept and writes the companies, people and their relationships to a CSV file.
      </p>
      <div class="row">
        <div class="col-md-6">
          <number-field
            path="extraction.dpi"
            label="Image quality"
            help="Sharper images help with small print, but use more memory and time."
            suffix="dpi"
          />
        </div>
        <div class="col-md-6">
          <number-field
            path="extraction.workers"
            label="Pages sent to the model at the same time"
            help="Automatic sends as many as the model server can work on at once. More than that only makes pages wait."
            empty-label="Automatic"
            suffix="at once"
          />
        </div>
      </div>
      <budget-list-field
        path="extraction.max_tokens_schedule"
        label="Length of the model’s answer, for each attempt"
        help="A limit on how much the model may write per page. A page whose answer is cut off is tried again with the next, larger limit. One limit per attempt, separated by commas."
      />
      <number-field
        path="extraction.tokens_per_entity_limit"
        label="Retry when an answer is long for what it found"
        help="Above this many tokens per company or person found, the page is read again: the model probably missed rows."
        suffix="tokens"
      />
    </collapsible-section>

    <collapsible-section
      v-model:open="open.sampling"
      title="Model sampling"
      description="Temperature, top p, top k and seed, for both finding and reading pages."
      :badge="changed('sampling')"
    >
      <p class="small text-secondary mb-3">
        Leave these empty to use the model server’s own settings. A fixed seed with a temperature of 0 gives the
        most repeatable results.
      </p>
      <div class="row">
        <div class="col-md-6">
          <number-field path="sampling.temperature" label="Temperature" empty-label="Server default" :step="0.1" />
        </div>
        <div class="col-md-6">
          <number-field path="sampling.top_p" label="Top p" empty-label="Server default" :step="0.05" />
        </div>
        <div class="col-md-6">
          <number-field path="sampling.top_k" label="Top k" empty-label="Server default" />
        </div>
        <div class="col-md-6">
          <number-field path="sampling.seed" label="Seed" empty-label="Server default" />
        </div>
      </div>
    </collapsible-section>

    <collapsible-section
      v-model:open="open.computer"
      title="This computer"
      description="The system, memory and graphics the app found, which decide the recommended model."
    >
      <computer-facts />
    </collapsible-section>

    <collapsible-section
      v-if="models.mode === 'app'"
      v-model:open="open.own"
      title="Use my own model server"
      description="For people who already run llama.cpp or vLLM on this computer."
    >
      <p class="small text-secondary mb-3">
        The app then neither downloads nor starts a model: it sends the pages to the server at the address you
        give. Only addresses on this computer are accepted.
      </p>
      <button-icon
        variant="outline-secondary"
        :icon-left="IPhHardDrives"
        label="Use a model server I started myself"
        @click="models.setMode('own')"
      />
    </collapsible-section>

    <step-footer />
  </div>
</template>

<style scoped lang="scss">
.settings-step__defaults p {
  min-width: 18rem;
}
</style>
