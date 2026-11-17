<script setup lang="ts">
import { computed, ref, useId, watch } from 'vue'

import { useConfigStore } from '@/stores/config'

import GuideField from './GuideField.vue'

// A text answer with a few ready-made choices and "Other", which opens a box to fill in.

const props = defineProps<{
  path: string
  label: string
  help?: string
  options: { label: string; value: string }[]
  otherLabel?: string
  otherPlaceholder?: string
}>()

const config = useConfigStore()
const name = useId()
const otherId = useId()

const value = computed(() => String(config.get(props.path) ?? ''))
const listed = (text: string) => props.options.some((option) => option.value === text)

const isOther = ref(!listed(value.value))
// What was typed in the box: choosing a ready-made answer and coming back finds it again
const typed = ref(isOther.value ? value.value : '')

// A change that did not come from the box (a preset, a reset) decides which choice is selected
watch(value, (text) => {
  if (isOther.value && text === typed.value) return
  isOther.value = !listed(text)
  if (isOther.value) typed.value = text
})

function choose(answer: string) {
  isOther.value = false
  config.set(props.path, answer)
}

function chooseOther() {
  isOther.value = true
  config.set(props.path, typed.value)
}

function type(event: Event) {
  const text = (event.target as HTMLInputElement).value
  isOther.value = true
  typed.value = text
  config.set(props.path, text)
}

function reset() {
  config.resetToDefault(props.path)
  isOther.value = !listed(String(config.get(props.path) ?? ''))
}
</script>

<template>
  <guide-field
    :label="label"
    :help="help"
    :changed="!config.isDefault(path)"
    :messages="config.messagesFor(path)"
    @reset="reset"
  >
    <div v-for="(option, index) in options" :key="option.value" class="form-check">
      <input
        :id="`${name}-${index}`"
        class="form-check-input"
        type="radio"
        :name="name"
        :checked="!isOther && value === option.value"
        @change="choose(option.value)"
      />
      <label :for="`${name}-${index}`" class="form-check-label">{{ option.label }}</label>
    </div>
    <div class="d-flex flex-wrap align-items-center gap-2">
      <div class="form-check mb-0">
        <input :id="otherId" class="form-check-input" type="radio" :name="name" :checked="isOther" @change="chooseOther" />
        <label :for="otherId" class="form-check-label">{{ otherLabel ?? 'Other' }}</label>
      </div>
      <input
        type="text"
        class="form-control form-control-sm w-auto flex-grow-1 other-answer"
        :value="isOther ? value : typed"
        :placeholder="otherPlaceholder"
        :aria-label="`${label} (other)`"
        :class="{ 'is-invalid': isOther && config.messagesFor(path).length }"
        @focus="isOther || chooseOther()"
        @input="type"
      />
    </div>
  </guide-field>
</template>

<style scoped>
.other-answer {
  max-width: 24rem;
}
</style>
