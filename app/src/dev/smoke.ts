// Development only (see app/src-tauri/src/dev.rs): a scripted pass through the
// app in a Tauri window, through the same stores the screens use. Runs only
// when the app was started with TABLE_EXTRACT_SMOKE_TEST.

import type { Pinia } from 'pinia'
import { nextTick, watch } from 'vue'

import { platform } from '@/platform'
import { useConfigStore } from '@/stores/config'
import { useRunStore } from '@/stores/run'
import { useSidecarStore } from '@/stores/sidecar'
import { useUiStore } from '@/stores/ui'

interface SmokeConfig {
  inputs: string[]
  output_dir: string
  server_url: string
}

function until(check: () => boolean, what: string, timeout = 90_000): Promise<void> {
  return new Promise((resolve, reject) => {
    if (check()) return resolve()
    const timer = setTimeout(() => {
      stop()
      reject(new Error(`timed out waiting for ${what}`))
    }, timeout)
    const stop = watch(check, (done) => {
      if (!done) return
      clearTimeout(timer)
      stop()
      resolve()
    })
  })
}

export async function runSmokeTest(pinia: Pinia): Promise<void> {
  const { invoke } = await import('@tauri-apps/api/core')
  const smoke = await invoke<SmokeConfig | null>('dev_smoke_config')
  if (!smoke) return

  const log: string[] = []
  const step = (text: string) => log.push(`- ${text}`)
  const check = (ok: unknown, text: string) => {
    if (!ok) throw new Error(`check failed: ${text}`)
    step(text)
  }
  const sidecar = useSidecarStore(pinia)
  const config = useConfigStore(pinia)
  const run = useRunStore(pinia)
  const awake = () => invoke<boolean>('dev_keep_awake_active')
  // Edits schedule a check of their own a tick later; let that happen first
  const checkNow = async () => {
    await nextTick()
    return config.validateNow()
  }

  try {
    await until(() => sidecar.ready && config.config !== null, 'the app to load')
    check(sidecar.health?.app === 'table_extract', `connected to the sidecar through tauri-plugin-http (${sidecar.health?.version})`)
    useUiStore(pinia).go('run')

    config.startOver()
    config.addInputs(smoke.inputs)
    config.set('server.url', smoke.server_url)
    config.set('triage.workers', 1)
    config.set('triage.dpi', 72)
    config.set('triage.prompt.instructions', `${config.get('triage.prompt.instructions')}\nAlso count share registers as relevant.\n`)
    config.setOutputDir(smoke.output_dir)
    let checked = await checkNow()
    check(checked?.valid && checked.output?.state === 'new', 'settings valid, new results folder')
    check(config.pdfs.length === smoke.inputs.length, `${config.pdfs.length} PDF found`)

    check(await run.start(), 'run started')
    await until(() => run.events.some((e) => e.event === 'page_done'), 'the first page')
    check(run.connection === 'live', 'progress streams over SSE')
    check(await awake(), 'computer kept awake during the run')
    await run.cancel()
    await until(() => run.finished, 'the run to stop', 30_000)
    check(run.result === 'cancelled', 'run stopped by cancel')
    await until(() => !config.validating && config.outputState?.state === 'resume', 'the folder to show "resume"', 10_000)
    check(!(await awake()), 'keep-awake released')

    check(await run.start(), 'run continued')
    await until(() => run.finished, 'the continued run to finish')
    const pages = run.events.filter((e) => e.event === 'page_done').length
    check(run.result === 'done', 'continued run finished')
    check(pages === 1, `continued run read only the page left (${pages})`)

    config.set('triage.dpi', 80)
    checked = await checkNow()
    check(checked?.output?.state === 'conflict', 'changed setting: the folder holds a run with other settings')
    check(!(await run.start()) && run.conflict !== null, 'start refused until the user chooses')
    check(await run.start(true), 'overwrite started')
    await until(() => run.finished, 'the overwriting run to finish')
    check(run.result === 'done', 'overwriting run finished')

    await platform.openFolder(smoke.output_dir)
    step('results folder opened in the file manager')
    const refused = await platform.openFolder(`${smoke.output_dir}/run.json`).then(
      () => false,
      () => true,
    )
    check(refused, 'open_folder refuses a file')

    config.startOver() // the next normal launch starts from the defaults, not from this test
    await invoke('dev_smoke_report', { ok: true, log: log.join('\n') })
  } catch (error) {
    log.push(`! ${error instanceof Error ? error.message : String(error)}`)
    await invoke('dev_smoke_report', { ok: false, log: log.join('\n') })
  }
}
