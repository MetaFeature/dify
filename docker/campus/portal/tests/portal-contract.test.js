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

test('the portal header does not show a campus-network hint', () => {
  assert.doesNotMatch(page, /校园内网|network-badge/)
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

test('only sanitized presentation content is inserted into the Portal origin', () => {
  const assignments = [...script.matchAll(/^\s*(\S+)\.innerHTML = (.+)$/gm)].map(match => match[2].trim())

  assert.deepEqual(assignments, ['portalPresentation.login_html'])
  assert.doesNotMatch(script, /chapter\.body_html/)
})

test('learning-document titles use the backend-issued isolated-origin URL', () => {
  const manual = script.slice(script.indexOf('async function showManual('))

  assert.match(manual, /link\.textContent = chapter\.title/)
  assert.match(manual, /link\.href = chapter\.content_url/)
  assert.match(manual, /link\.rel = 'noopener'/)
})

test('reservation history provides accessible pagination controls', () => {
  assert.match(page, /<nav id="reservation-pagination"[^>]*aria-label="预约记录分页"/)
  assert.match(page, /<button id="reservation-previous"[^>]*>上一页<\/button>/)
  assert.match(page, /<span id="reservation-page"[^>]*aria-live="polite"/)
  assert.match(page, /<button id="reservation-next"[^>]*>下一页<\/button>/)
})
