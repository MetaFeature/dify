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
