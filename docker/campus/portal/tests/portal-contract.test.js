import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const page = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const script = await readFile(new URL('../assets/portal.js', import.meta.url), 'utf8')
const noscriptCss = await readFile(new URL('../assets/portal-noscript.css', import.meta.url), 'utf8')

test('every element portal.js requires exists in index.html', () => {
  const required = [...script.matchAll(/requiredElement\('#([a-z-]+)'/g)].map(match => match[1])

  assert.ok(required.length > 0, 'portal.js must declare its required elements')
  const missing = required.filter(id => !page.includes(`id="${id}"`))
  assert.deepEqual(missing, [], `index.html is missing required elements: ${missing.join(', ')}`)
})

test('a reload never paints the sign-in form before the session is known', async () => {
  // The portal can only learn whether the visitor has a session from an API
  // round trip. Painting #login-view first made every reload flash the login
  // page at students who were still signed in, so <body> opens with `booting`
  // and portal.js clears it only once the probe has settled.
  const css = await readFile(new URL('../assets/portal.css', import.meta.url), 'utf8')

  assert.match(page, /<body class="booting">/)
  assert.match(css, /body\.booting #login-view \{ display: none; \}/)
  // Without modules the class would never be removed, so a browser that runs no
  // JavaScript has to get the form back. That fallback must be a stylesheet the
  // CSP allows — `style-src 'self'` refuses an inline <style> block, which is
  // how the first attempt at this silently did nothing.
  assert.match(page, /<noscript><link rel="stylesheet" href="\.\/assets\/portal-noscript\.css"><\/noscript>/)
  assert.doesNotMatch(page, /<noscript><style>/)
  assert.match(noscriptCss, /body\.booting #login-view \{ display: grid; \}/)

  const bootstrap = script.slice(script.indexOf('async function bootstrapPortal('))
  const block = bootstrap.slice(0, bootstrap.indexOf('\n}'))
  assert.ok(
    block.indexOf('await restoreSession()') < block.indexOf("classList.remove('booting')"),
    'the login view may only be revealed after the session probe has answered',
  )
  assert.match(block, /finally \{/)
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

test('only one of the two signed-in views is ever shown at a time', () => {
  for (const shown of ['showTracks', 'showReservations']) {
    const body = script.slice(script.indexOf(`async function ${shown}(`))
    const block = body.slice(0, body.indexOf('\n}'))
    const hidden = [...block.matchAll(/elements\.(\w+View)\.hidden = (true|false)/g)]

    assert.equal(hidden.filter(([, , value]) => value === 'false').length, 1, `${shown} must show one view`)
    assert.equal(hidden.filter(([, , value]) => value === 'true').length, 1, `${shown} must hide the other`)
  }
})

test('signing out hides every signed-in view', () => {
  const signOut = script.slice(script.indexOf('elements.loginView.hidden = false') - 400, script.indexOf('elements.loginView.hidden = false'))

  for (const view of ['dashboardView', 'tracksView'])
    assert.match(signOut, new RegExp(`elements\\.${view}\\.hidden = true`))
})

test('only sanitized presentation content is inserted into the Portal origin', () => {
  const assignments = [...script.matchAll(/^\s*(\S+)\.innerHTML = (.+)$/gm)].map(match => match[2].trim())

  assert.deepEqual(assignments, ['portalPresentation.login_html'])
  assert.doesNotMatch(script, /chapter\.body_html/)
})

test('a chapter title links straight at the backend-issued manual origin', () => {
  // No reader page any more: the chooser links at the isolated origin itself,
  // and the URL is the one the backend built rather than a client guess.
  const chooser = script.slice(script.indexOf('function chapterLinks('))

  assert.match(chooser, /link\.href = chapter\.view_url/)
  assert.match(chooser, /title\.textContent = chapter\.title/)
  // The derived teaser rides along under the title.
  assert.match(chooser, /summary\.textContent = chapter\.summary/)
  assert.doesNotMatch(script, /showManual|manualBody|manualChapters/)
  assert.doesNotMatch(script, /chapter\.content_url|chapter\.body_html/)
})

test('each experiment is its own row with the chapter titles in two columns', async () => {
  const css = await readFile(new URL('../assets/portal.css', import.meta.url), 'utf8')
  const cards = css.slice(css.indexOf('.track-cards {'), css.indexOf('.track-card p'))
  const links = css.slice(css.indexOf('.chapter-links {'), css.indexOf('.chapter-link:hover'))

  assert.match(cards, /grid-template-columns: 1fr;/)
  assert.match(links, /grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/)
})

test('a reload or a Back/Forward restores a live session instead of signing out', () => {
  const boot = script.slice(script.indexOf('async function bootstrapPortal('))
  const bootBody = boot.slice(0, boot.indexOf('\n}'))
  const restore = script.slice(script.indexOf('async function restoreSession('))
  const body = restore.slice(0, restore.indexOf('\n}\n'))

  // The session decides, not a URL marker.
  assert.match(bootBody, /await restoreSession\(\)/)
  assert.doesNotMatch(script, /from=manual|restoreTracksFromManual/)
  assert.match(body, /await api\.listExperimentTracks\(\)/)
  assert.match(body, /elements\.loginView\.hidden = true/)
  assert.match(body, /elements\.tracksView\.hidden = false/)
  assert.match(body, /rememberView\('tracks', \{ replace: true \}\)/)
})

test('each view gets a history entry so Back stays inside the portal', () => {
  const remember = script.slice(script.indexOf('function rememberView('))
  const body = remember.slice(0, remember.indexOf('\n}\n'))

  assert.match(body, /window\.history\.pushState\(entry, '', '\/portal\/'\)/)
  assert.match(body, /window\.history\.replaceState\(entry, '', '\/portal\/'\)/)
  // Back and Forward replay the view instead of leaving the site.
  assert.match(script, /addEventListener\('popstate'/)
  assert.match(script, /event\.state\?\.view === 'dashboard'/)
  assert.match(script, /void showTracks\(\{ replace: true \}\)/)
  assert.match(script, /rememberView\('tracks', \{ replace \}\)/)
  assert.match(script, /rememberView\('dashboard', \{ replace \}\)/)
})

test('reservation history provides accessible pagination controls', () => {
  assert.match(page, /<nav id="reservation-pagination"[^>]*aria-label="预约记录分页"/)
  assert.match(page, /<button id="reservation-previous"[^>]*>上一页<\/button>/)
  assert.match(page, /<span id="reservation-page"[^>]*aria-live="polite"/)
  assert.match(page, /<button id="reservation-next"[^>]*>下一页<\/button>/)
})

test('the chooser grey line links each experiment name to its own card', async () => {
  const line = page.slice(page.indexOf('<p class="muted"><a class="track-jump"'), page.indexOf('<button id="tracks-refresh"'))
  const tracks = [...line.matchAll(/data-jump-track="([a-z-]+)"/g)].map(match => match[1])

  assert.deepEqual(tracks, ['large-model', 'agent', 'deep-learning'])
  // The line says what happens where, and nothing else.
  assert.doesNotMatch(line, /每个实验下列出|三个实验均可查看/)
})

test('a jump link centres the matching card instead of jumping to its top', () => {
  const jump = script.slice(script.indexOf('function centreTrackCard('))
  const body = jump.slice(0, jump.indexOf('\n}'))

  assert.match(body, /getElementById\(`track-\$\{track\}`\)/)
  assert.match(body, /scrollIntoView\(\{ behavior: 'smooth', block: 'center' \}\)/)
  // Cards are rebuilt on every refresh, so the listener cannot be bound to them.
  assert.match(script, /document\.addEventListener\('click', \(event\) => \{[\s\S]{0,200}?data-jump-track/)
})

test('the reservation button sits under the description, above the chapter titles', () => {
  const render = script.slice(script.indexOf('function renderTrackCards('))
  const append = render.slice(render.indexOf('item.append('), render.indexOf('\n', render.indexOf('item.append(')))

  assert.match(append, /item\.append\(heading, detail, \.\.\.\(actions \? \[actions\] : \[\]\), chapterLinks\(card\)\)/)
})

test('a jump turns the card title bright and eases it back on its own', async () => {
  const css = await readFile(new URL('../assets/portal.css', import.meta.url), 'utf8')
  const jump = script.slice(script.indexOf('function flashTrackCard('))
  const body = jump.slice(0, jump.indexOf('\n}\n'))

  // Only the card's title brightens: not the card surface, not the grey
  // line's link, and not the description or chapter titles underneath.
  assert.match(css, /\.track-card\.is-flashing h2 \{ color: var\(--ds-color-brand\); \}/)
  assert.doesNotMatch(css, /\.track-card\.is-flashing > p/)
  assert.doesNotMatch(css, /\.track-card\.is-flashing \.chapter-link/)
  assert.doesNotMatch(css, /\.track-jump\.is-flashing/)
  // It clears itself, so the card is back to normal without another click.
  assert.match(body, /window\.setTimeout\(\(\) => \{/)
  assert.match(body, /card\.classList\.remove\('is-flashing'\)/)
  // Only the newest card is ever bright.
  assert.match(body, /if \(flashingCard && flashingCard !== card\)/)
})

test('the chapter teaser is clamped to two lines under its title', async () => {
  const css = await readFile(new URL('../assets/portal.css', import.meta.url), 'utf8')
  const rule = css.slice(css.indexOf('.chapter-link-summary {'), css.indexOf('.chapter-empty'))

  assert.match(rule, /-webkit-line-clamp: 2;/)
  assert.match(rule, /display: -webkit-box;/)
  assert.match(rule, /overflow: hidden;/)
  assert.match(css, /\.chapter-link \{[\s\S]{0,60}?display: grid;/)
})
