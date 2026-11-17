/** The app's name. It also sets the default results folder. */
export const APP_NAME = 'LocalHarvest'

/** ~/Documents/<App name>/<date>_<model>/ (docs/gui-design.md, Scope). */
export function suggestedOutputDir(documents: string, model: string, date = new Date()): string {
  const day = [date.getFullYear(), date.getMonth() + 1, date.getDate()].map((n) => String(n).padStart(2, '0')).join('-')
  const safeModel = (model.split('/').pop() || 'model').replace(/[^A-Za-z0-9._-]+/g, '-')
  const separator = documents.includes('\\') && !documents.includes('/') ? '\\' : '/'
  return [documents.replace(/[\\/]+$/, ''), APP_NAME, `${day}_${safeModel}`].join(separator)
}

/** Who makes the app and who carries the project, for the About panel. Placeholders, to be written. */
export const CREDITS = {
  editor: 'OsloMet - ICIJ - NRK',
  project: 'OsloMet Social Innovation Grant',
}
