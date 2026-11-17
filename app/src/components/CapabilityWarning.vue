<script setup lang="ts">
import { AppIcon } from '@icij/murmur'
import IPhWarningFill from '~icons/ph/warning-fill'
import IPhWarningOctagonFill from '~icons/ph/warning-octagon-fill'
import { computed } from 'vue'

import { formatBytes } from '@/stores/hardware'
import { useModelsStore } from '@/stores/models'
import { useUiStore } from '@/stores/ui'

// A clear warning when this computer cannot run the model, or only very slowly:
// at the top of Settings, and in short on the welcome screen.

const props = defineProps<{
  /** The short version, with a link to Settings. */
  compact?: boolean
}>()

const models = useModelsStore()
const ui = useUiStore()

const BETTER = 'A Mac with Apple silicon or a PC with an NVIDIA graphics card works best.'

const warning = computed(() => {
  const c = models.capability
  if (models.mode !== 'app' || !c) return null
  if (c.level === 'cannot') {
    const need = c.smallestBytes ? `about ${formatBytes(c.smallestBytes)}` : 'more memory'
    return {
      tone: 'danger',
      icon: IPhWarningOctagonFill,
      title: 'This computer cannot run the model',
      text: `It does not have enough memory: even the smallest model the app knows needs ${need} to itself, `
        + `and this computer has ${formatBytes(c.ramBytes)} in all.`,
      advice: `Use a computer with more memory. ${BETTER}`,
    }
  }
  const why = {
    no_gpu: c.early
      ? 'No graphics processor was found, so the model would run on the processor alone.'
      : 'The model cannot use a graphics processor on this computer, so it runs on the processor alone.',
    small_gpu: 'The graphics processor has too little memory for the model, so most of it would run on the processor.',
    memory: '',
  }[c.reason]
  return {
    tone: 'warning',
    icon: IPhWarningFill,
    title: 'Runs will be very slow on this computer',
    text: `${why} That works, but very slowly: a single page can take minutes, and a folder of documents many hours.`,
    advice: `If you can, use a computer with a graphics processor. ${BETTER}`
      + (c.early ? ' The app checks again once the program that runs the model is installed.' : ''),
  }
})
</script>

<template>
  <div
    v-if="warning"
    class="capability-warning alert d-flex gap-3 align-items-start"
    :class="[`alert-${warning.tone}`, { 'capability-warning--compact': props.compact }]"
  >
    <app-icon :name="warning.icon" size="xl" class="flex-shrink-0 mt-1" />
    <div class="flex-grow-1">
      <p class="fw-semibold mb-1">{{ warning.title }}</p>
      <p class="mb-1">{{ warning.text }}</p>
      <p v-if="!props.compact" class="mb-0">{{ warning.advice }}</p>
      <button v-else type="button" class="btn btn-link btn-sm p-0 align-baseline" @click="ui.go('settings')">
        See the details in Settings
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.capability-warning {
  &--compact {
    max-width: 46rem;
  }
}
</style>
