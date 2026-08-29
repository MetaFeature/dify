import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const script = await readFile(new URL('../assets/portal.js', import.meta.url), 'utf8')

test('every element portal.js requires exists in index.html', () => {
  const required = [...script.matchAll(/requiredElement\('#([a-z-]+)'/g)].map(match => match[1])

  assert.ok(required.length > 0, 'portal.js must declare its required elements')
  const missing = required.filter(id => !page.includes(`id="${id}"`))
  assert.deepEqual(missing, [], `index.html is missing required elements: ${missing.join(', ')}`)
})

test('the login form cannot submit natively, so credentials never reach the URL', () => {
  assert.match(script, /#login-form[\s\S]{0,400}?addEventListener\('submit', event => event\.preventDefault\(\)\)/)
})

test('signing in lands on the experiment selection page, not straight in the reservation centre', () => {
  // The reservation centre is one of three destinations now; going there
  // directly would hide the other two tracks from every student.
  const loginHandler = script.slice(script.indexOf('await api.login('), script.indexOf('catch (error)'))

  assert.match(loginHandler, /showTracks\(\)/)
  assert.doesNotMatch(loginHandler, /dashboardView\.hidden = false/)
})

test('only one of the three views is ever shown at a time', () => {
  for (const shown of ['showTracks', 'showReservations', 'showManual']) {
    const body = script.slice(script.indexOf(`async function ${shown}(`))
    const block = body.slice(0, body.indexOf('\n}'))
    const hidden = [...block.matchAll(/elements\.(\w+View)\.hidden = (true|false)/g)]

    assert.equal(hidden.filter(([, , value]) => value === 'false').length, 1, `${shown} must show one view`)
    assert.equal(hidden.filter(([, , value]) => value === 'true').length, 2, `${shown} must hide the other two`)
  }
})

test('signing out hides every signed-in view', () => {
  const signOut = script.slice(script.indexOf('elements.loginView.hidden = false') - 400, script.indexOf('elements.loginView.hidden = false'))

  for (const view of ['dashboardView', 'tracksView', 'manualView'])
    assert.match(signOut, new RegExp(`elements\\.${view}\\.hidden = true`))
})

test('only the chapter body is assigned as HTML, and it says why', () => {
  // Chapter HTML is sanitized at upload. Any other innerHTML on this page would
  // be a second, unreviewed path for markup to reach a student.
  const assignments = [...script.matchAll(/^\s*(\S+)\.innerHTML = (.+)$/gm)].map(match => match[2].trim())

  assert.deepEqual(assignments, ['chapter.body_html'])
  assert.match(script, /Sanitized at upload; see services\/campus\/lab_manual_html\.py\./)
})

test('chapter titles are assigned as text, never as markup', () => {
  const manual = script.slice(script.indexOf('async function showManual('))

  assert.match(manual, /link\.textContent = chapter\.title/)
  assert.match(script, /heading\.textContent = chapter\.title/)
})
