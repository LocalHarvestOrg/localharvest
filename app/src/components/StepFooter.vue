<script setup lang="ts">
import { ButtonIcon } from '@icij/murmur'
import IPhArrowLeft from '~icons/ph/arrow-left'
import IPhArrowRight from '~icons/ph/arrow-right'

import { STEPS, useUiStore } from '@/stores/ui'

const ui = useUiStore()
const index = () => STEPS.findIndex((s) => s.name === ui.step)
</script>

<template>
  <footer class="step-footer d-flex justify-content-between border-top pt-3 mt-4">
    <button-icon
      v-if="index() > 0"
      variant="outline-secondary"
      :icon-left="IPhArrowLeft"
      :label="`Back: ${STEPS[index() - 1].title}`"
      @click="ui.previous()"
    />
    <span v-else />
    <button-icon
      v-if="index() < STEPS.length - 1"
      variant="action"
      :icon-right="IPhArrowRight"
      :label="`Next: ${STEPS[index() + 1].title}`"
      @click="ui.next()"
    />
  </footer>
</template>
