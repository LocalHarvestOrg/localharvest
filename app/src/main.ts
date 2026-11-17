import './styles/main.scss'

import { i18n as murmurI18n } from '@icij/murmur'
import { createBootstrap, vBTooltip } from 'bootstrap-vue-next'
import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { isTauri } from './platform'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia)
app.use(createBootstrap())
app.directive('b-tooltip', vBTooltip) // Murmur's buttons use it without registering it
app.use(murmurI18n) // Murmur's components translate their own labels
app.mount('#app')

// Development only: the scripted check of app/src-tauri/src/dev.rs
if (import.meta.env.DEV && isTauri()) {
  void import('./dev/smoke').then(({ runSmokeTest }) => runSmokeTest(pinia))
}
