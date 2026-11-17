import { describe, expect, it } from 'vitest'

import type { MemoryBudget, ModelOption, Recommendation, RuntimeStatus, Verdict } from '@/api/types'
import { assessCapability } from '@/stores/capability'

const GB = 1024 ** 3

function option(id: string, verdict: Verdict, need: number): ModelOption {
  return {
    id,
    label: id,
    description: '',
    repo: 'unsloth/model-GGUF',
    revision: 'abc',
    quant: 'Q4_K_M',
    context: 16384,
    tested: true,
    model_id: `unsloth/${id}:Q4_K_M`,
    installed: false,
    download_bytes: need,
    estimate: { context: 16384, parts: {}, total_bytes: need, verdict },
    benchmark: null,
  }
}

function recommendation(
  options: ModelOption[],
  model: ModelOption | null,
  memory: Partial<MemoryBudget> = {},
): Recommendation {
  return {
    model,
    reason: '',
    options,
    memory: {
      gpu_bytes: 0,
      ram_bytes: 16 * GB,
      unified_memory: false,
      has_gpu: false,
      gpu_source: null,
      known: true,
      ...memory,
    },
  }
}

function runtime(vendors: string[]): RuntimeStatus {
  return {
    pinned_build: 'b1',
    installed: null,
    llama_server: null,
    source: null,
    build: null,
    update_available: false,
    gpu_vendors: vendors,
    recommended_variant: null,
    variants: [],
    job: null,
  }
}

describe('assessCapability', () => {
  it('says nothing before the recommendation is loaded', () => {
    expect(assessCapability(null, null)).toBeNull()
  })

  it('says nothing when a model fits on the GPU', () => {
    const fits = option('gemma', 'gpu', 8 * GB)
    const rec = recommendation([fits], fits, { gpu_bytes: 12 * GB, has_gpu: true, unified_memory: true })
    expect(assessCapability(rec, runtime(['apple']))).toBeNull()
  })

  it('cannot run when no model fits, with the smallest need and the memory there is', () => {
    const rec = recommendation([option('big', 'too_big', 19 * GB), option('small', 'too_big', 8 * GB)], null, {
      ram_bytes: 8 * GB,
    })
    expect(assessCapability(rec, runtime([]))).toEqual({
      level: 'cannot',
      reason: 'memory',
      early: false,
      smallestBytes: 8 * GB,
      ramBytes: 8 * GB,
    })
  })

  it('warns that runs are very slow without a GPU', () => {
    const cpu = option('gemma', 'cpu', 8 * GB)
    expect(assessCapability(recommendation([cpu], cpu), runtime([]))).toMatchObject({
      level: 'cpu',
      reason: 'no_gpu',
      early: false,
    })
  })

  it('warns when less than half the model fits in a separate GPU’s memory', () => {
    const partial = option('gemma', 'partial', 8 * GB)
    const small = recommendation([partial], partial, { gpu_bytes: 2 * GB, has_gpu: true })
    expect(assessCapability(small, runtime(['intel']))).toMatchObject({ level: 'cpu', reason: 'small_gpu' })

    // Most of it on the GPU: slower, which the model's badge says, but no warning
    const most = recommendation([partial], partial, { gpu_bytes: 6 * GB, has_gpu: true })
    expect(assessCapability(most, runtime(['nvidia']))).toBeNull()
  })

  it('does not warn for a partial fit on unified memory (a Mac)', () => {
    const partial = option('gemma', 'partial', 8 * GB)
    const rec = recommendation([partial], partial, { gpu_bytes: 3 * GB, has_gpu: true, unified_memory: true })
    expect(assessCapability(rec, runtime(['apple']))).toBeNull()
  })

  describe('before llama.cpp is installed (Windows, Linux)', () => {
    const unknown = (verdict: Verdict) => recommendation([option('gemma', verdict, 8 * GB)], null, { known: false })

    it('warns early when no GPU maker is found', () => {
      expect(assessCapability(unknown('unknown'), runtime([]))).toMatchObject({
        level: 'cpu',
        reason: 'no_gpu',
        early: true,
      })
    })

    it('waits for llama.cpp when a GPU is found, or when the GPU makers are not known', () => {
      expect(assessCapability(unknown('unknown'), runtime(['nvidia']))).toBeNull()
      expect(assessCapability(unknown('unknown'), null)).toBeNull()
    })

    it('cannot run when even system memory is too small', () => {
      expect(assessCapability(unknown('too_big'), runtime(['intel']))).toMatchObject({
        level: 'cannot',
        early: true,
      })
    })
  })
})
