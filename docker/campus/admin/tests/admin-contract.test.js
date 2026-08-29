import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const script = await readFile(new URL('../assets/admin.js', import.meta.url), 'utf8')

test('every element admin.js requires exists in index.html', () => {
  const required = [...script.matchAll(/requiredElement\('#([a-z-]+)'/g)].map(match => match[1])

  assert.ok(required.length > 0, 'admin.js must declare its required elements')
  const missing = required.filter(id => !page.includes(`id="${id}"`))
  assert.deepEqual(missing, [], `index.html is missing required elements: ${missing.join(', ')}`)
})

test('the auth recovery control is a button, not a bare sign-in link that can bounce', () => {
  assert.match(page, /<button id="auth-action"/)
  assert.doesNotMatch(page, /<a[^>]+class="primary-link"[^>]+href="\/signin"/)
})

test('every tab button has the panel the switcher will look for', () => {
  const tabs = [...page.matchAll(/data-tab="([a-z-]+)"/g)].map(match => match[1])

  assert.ok(tabs.length > 0, 'index.html must declare tab buttons')
  const missing = tabs.filter(name => !page.includes(`id="tab-${name}"`))
  assert.deepEqual(missing, [], `index.html is missing tab panels: ${missing.join(', ')}`)
})

test('the model gateway tab keeps the pricing guardrail in front of the administrator', () => {
  // Adding a model without a ratio makes the gateway bill at its fallback,
  // roughly five hundred times the real price. The tab that sends an
  // administrator to the gateway must say so.
  const panel = page.slice(page.indexOf('id="tab-gateway"'))

  assert.match(panel, /倍率/)
  assert.match(panel, /manage\.sh verify/)
})

test('the lab manual tab offers only the two tracks that have a manual', () => {
  // The large-model track is completed in Dify; offering it here would let an
  // administrator author a manual the platform never serves.
  const panel = page.slice(page.indexOf('id="tab-manuals"'), page.indexOf('id="tab-gateway"'))
  const options = [...panel.matchAll(/<option value="([a-z-]+)"/g)].map(match => match[1])

  assert.deepEqual(options, ['deep-learning', 'agent'])
})

test('the manual tab tells the administrator their formatting will not survive', () => {
  // Sanitizing strips inline styles. Not saying so is what makes an upload look
  // broken to whoever wrote it.
  const panel = page.slice(page.indexOf('id="tab-manuals"'), page.indexOf('id="tab-gateway"'))

  assert.match(panel, /清洗/)
  assert.match(panel, /排版不会生效/)
})

test('a saved chapter always reports what sanitizing removed', () => {
  const submit = script.slice(script.indexOf("elements.manualForm.addEventListener('submit'"))
  const handler = submit.slice(0, submit.indexOf('\n})'))

  assert.match(handler, /renderSanitizeReport\(saved\.removed\)/)
})

test('editing a chapter reads its body rather than opening an empty form', () => {
  // The list omits body_html; a save from an empty form would wipe the chapter.
  const editor = script.slice(script.indexOf('async function startEditingChapter('))
  const body = editor.slice(0, editor.indexOf('\n}'))

  assert.match(body, /api\.manualChapter\(chapterId\)/)
  assert.match(body, /elements\.manualHtml\.value = chapter\.body_html/)
})

test('chapter titles reach the table escaped', () => {
  // Titles are administrator input rendered into an innerHTML template.
  const loader = script.slice(script.indexOf('async function loadManualChapters('))
  const body = loader.slice(0, loader.indexOf('\n}\n'))

  assert.match(body, /escapeHtml\(chapter\.title\)/)
  assert.doesNotMatch(body, /\$\{chapter\.title\}/)
})
