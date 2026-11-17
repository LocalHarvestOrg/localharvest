import type { ModelOption, Recommendation, RuntimeStatus } from '@/api/types'

/**
 * Whether this computer can run a local model at a usable speed, from the
 * sidecar's memory estimates (GET /models/recommendation) and the GPU makers it
 * finds before llama.cpp is installed (GET /runtime).
 *
 * "cannot": no model the app knows fits in memory, even on the processor.
 * "cpu": the model would run on the processor alone, or mostly: it works, but very slowly.
 * null: nothing to warn about (or not known yet). A model that only partly fits
 * on the GPU keeps its "Slower on this computer" badge, without a warning.
 */
export type CapabilityLevel = 'cannot' | 'cpu'

/** memory: too little memory. no_gpu: no graphics processor. small_gpu: too little graphics memory, most runs on the processor. */
export type CapabilityReason = 'memory' | 'no_gpu' | 'small_gpu'

export interface Capability {
  level: CapabilityLevel
  reason: CapabilityReason
  /** The checks behind the warning are estimates made before llama.cpp could look at the GPU. */
  early: boolean
  /** The least memory any of the app's models needs. */
  smallestBytes: number | null
  ramBytes: number
}

/** Below this share of the model on the GPU, it runs at about the processor's speed. */
export const MOSTLY_ON_GPU = 0.5

function smallest(options: ModelOption[]): number | null {
  const sizes = options.map((o) => o.estimate.total_bytes).filter((b) => b > 0)
  return sizes.length ? Math.min(...sizes) : null
}

export function assessCapability(
  recommendation: Recommendation | null,
  runtime: RuntimeStatus | null,
): Capability | null {
  if (!recommendation) return null
  const { memory, options, model } = recommendation
  const base = { smallestBytes: smallest(options), ramBytes: memory.ram_bytes }

  if (!memory.known) {
    // Before llama.cpp is installed, on Windows and Linux: system memory and the GPU makers
    if (options.length && options.every((o) => o.estimate.verdict === 'too_big')) {
      return { level: 'cannot', reason: 'memory', early: true, ...base }
    }
    const vendors = runtime?.gpu_vendors
    if (vendors && vendors.length === 0) return { level: 'cpu', reason: 'no_gpu', early: true, ...base }
    return null
  }

  if (!model) return options.length ? { level: 'cannot', reason: 'memory', early: false, ...base } : null
  const { verdict, total_bytes: need } = model.estimate
  if (verdict === 'cpu') return { level: 'cpu', reason: 'no_gpu', early: false, ...base }
  if (verdict === 'partial' && !memory.unified_memory && memory.gpu_bytes < need * MOSTLY_ON_GPU) {
    return { level: 'cpu', reason: 'small_gpu', early: false, ...base }
  }
  return null
}
