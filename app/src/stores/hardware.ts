import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

import type { Hardware } from '@/api/types'

import { useSidecarStore } from './sidecar'

const GIB = 1024 ** 3

export function formatBytes(bytes: number): string {
  if (bytes >= GIB) return `${(bytes / GIB).toFixed(bytes >= 10 * GIB ? 0 : 1)} GB`
  return `${Math.round(bytes / 1024 ** 2)} MB`
}

/** What the computer has, for the Settings screen (GET /hardware takes about a second). */
export const useHardwareStore = defineStore('hardware', () => {
  const sidecar = useSidecarStore()
  const hardware = shallowRef<Hardware | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const gpus = computed(() => hardware.value?.devices.filter((d) => d.gpu) ?? [])

  async function load(force = false): Promise<void> {
    if ((hardware.value && !force) || loading.value) return
    loading.value = true
    error.value = null
    try {
      hardware.value = await sidecar.api.hardware()
    } catch (e) {
      error.value = e instanceof Error ? e.message : String(e)
    } finally {
      loading.value = false
    }
  }

  return { hardware, loading, error, gpus, load }
})
