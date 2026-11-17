import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { useUiStore } from '@/stores/ui'

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('welcome screen', () => {
  it('shows on first launch, and not once it has been left', () => {
    const ui = useUiStore()
    expect(ui.welcome).toBe(true)
    expect(ui.step).toBe('documents')
    ui.go('run')
    expect(ui.welcome).toBe(false)
    expect(localStorage.getItem('table-extract.welcome-seen')).toBe('true')

    setActivePinia(createPinia())
    expect(useUiStore().welcome).toBe(false)
  })

  it('comes back on request, and leaving it keeps the step', () => {
    const ui = useUiStore()
    ui.go('settings')
    ui.showWelcome()
    expect(ui.welcome).toBe(true)
    ui.leaveWelcome()
    expect(ui.welcome).toBe(false)
    expect(ui.step).toBe('settings')
  })
})
