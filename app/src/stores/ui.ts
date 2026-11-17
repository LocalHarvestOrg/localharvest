import { defineStore } from 'pinia'
import { ref } from 'vue'

import { readStorage, writeStorage } from './config'

export type StepName = 'documents' | 'settings' | 'run'

export const STEPS: { name: StepName; title: string; hint: string }[] = [
  { name: 'documents', title: 'Documents', hint: 'The PDFs to read' },
  { name: 'settings', title: 'Settings', hint: 'How pages are read' },
  { name: 'run', title: 'Run', hint: 'Start and follow' },
]

const WELCOME_SEEN_KEY = 'table-extract.welcome-seen'

export const useUiStore = defineStore('ui', () => {
  const step = ref<StepName>('documents')
  // The welcome screen comes before the steps, until it has been left once
  const welcome = ref(!readStorage<boolean>(WELCOME_SEEN_KEY))

  function go(name: StepName): void {
    step.value = name
    leaveWelcome()
  }

  function showWelcome(): void {
    welcome.value = true
  }

  function leaveWelcome(): void {
    if (!welcome.value) return
    welcome.value = false
    writeStorage(WELCOME_SEEN_KEY, true)
  }

  function next(): void {
    const index = STEPS.findIndex((s) => s.name === step.value)
    if (index < STEPS.length - 1) step.value = STEPS[index + 1].name
  }

  function previous(): void {
    const index = STEPS.findIndex((s) => s.name === step.value)
    if (index > 0) step.value = STEPS[index - 1].name
  }

  return { step, welcome, go, showWelcome, leaveWelcome, next, previous }
})
