import { CampusApi, CampusApiError } from './campus-api.js'
import { messageFor, passwordErrorMessage } from './error-messages.js'
import {
  addCampusDays,
  campusDay,
  canCancelReservation,
  createCampusClock,
  detectPromotions,
  experimentTrackCards,
  formatCountdown,
  isCurrentAccessSlot,
  launchWorkspace,
  paginateReservations,
  reservationTiming,
  visibleSlots,
} from './portal-domain.js'
import { messages } from './messages.js'

const POLL_INTERVAL_MS = 60_000
const PASSIVE_REFRESH_MIN_GAP_MS = 5_000
const URGENT_COUNTDOWN_MS = 5 * 60_000

const api = new CampusApi()

// Block native form submission before any lookup below can throw: a native GET
// would carry the student's password into the URL, browser history and access
// logs. This guard must stay above `elements` so a page/script version skew
// cannot leave the form falling back to navigation.
const loginFormElement = document.querySelector('#login-form')
if (loginFormElement instanceof HTMLFormElement)
  loginFormElement.addEventListener('submit', event => event.preventDefault())

const elements = {
  loginView: requiredElement('#login-view', HTMLElement),
  loginContent: requiredElement('#login-content', HTMLElement),
  logout: requiredElement('#portal-logout', HTMLButtonElement),
  tracksView: requiredElement('#tracks-view', HTMLElement),
  dashboardView: requiredElement('#dashboard-view', HTMLElement),
  trackList: requiredElement('#track-list', HTMLElement),
  tracksMessage: requiredElement('#tracks-message', HTMLElement),
  tracksRefresh: requiredElement('#tracks-refresh', HTMLButtonElement),
  dashboardBack: requiredElement('#dashboard-back', HTMLButtonElement),
  loginForm: requiredElement('#login-form', HTMLFormElement),
  loginError: requiredElement('#login-error', HTMLElement),
  refreshButton: requiredElement('#refresh-button', HTMLButtonElement),
  day: requiredElement('#slot-day', HTMLInputElement),
  slotList: requiredElement('#slot-list', HTMLElement),
  reservationList: requiredElement('#reservation-list', HTMLElement),
  reservationPagination: requiredElement('#reservation-pagination', HTMLElement),
  reservationPrevious: requiredElement('#reservation-previous', HTMLButtonElement),
  reservationPage: requiredElement('#reservation-page', HTMLElement),
  reservationNext: requiredElement('#reservation-next', HTMLButtonElement),
  launchButton: requiredElement('#launch-button', HTMLButtonElement),
  message: requiredElement('#dashboard-message', HTMLElement),
  accessState: requiredElement('#access-state', HTMLElement),
  accessDetail: requiredElement('#access-detail', HTMLElement),
  allowanceRemaining: requiredElement('#allowance-remaining', HTMLElement),
  allowanceDetail: requiredElement('#allowance-detail', HTMLElement),
  reservationState: requiredElement('#reservation-state', HTMLElement),
  reservationDetail: requiredElement('#reservation-detail', HTMLElement),
  reservationCountdown: requiredElement('#reservation-countdown', HTMLElement),
  passwordForm: requiredElement('#password-form', HTMLFormElement),
  passwordMessage: requiredElement('#password-message', HTMLElement),
}

let campusClock = createCampusClock(new Date().toISOString(), Date.now())
/** @type {import('./campus-api.js').Reservation[]} */
let lastReservations = []
let countdownTimer = 0
let lastRefreshAtMs = 0
let reservationPage = 0
let resizeFrame = 0
let portalPresentation = null

void bootstrapPortal()

async function bootstrapPortal() {
  try {
    await loadPortalPresentation()
    if (new URLSearchParams(window.location.search).get('logout') === '1') {
      // The manual viewer cannot call this origin's API, so it links here.
      await signOut()
      return
    }
    // Always ask the API who we are. A reload — or a Back/Forward — must not
    // drop a live session just because the URL carries no marker.
    await restoreSession()
  }
  finally {
    // Only now is the answer known. `booting` kept the sign-in form out of the
    // first paint; dropping it any earlier is what made a refresh flash the
    // login page at students whose session was perfectly valid.
    document.body.classList.remove('booting')
  }
}

async function loadPortalPresentation() {
  try {
    portalPresentation = await api.presentation()
    elements.loginContent.innerHTML = portalPresentation.login_html
  }
  catch {
    // The checked-in default remains visible when presentation loading fails.
  }
}

async function restoreSession() {
  try {
    const summaries = await api.listExperimentTracks()
    elements.loginView.hidden = true
    elements.logout.hidden = false
    elements.dashboardView.hidden = true
    elements.tracksView.hidden = false
    hideMessage(elements.tracksMessage)
    renderTrackCards(experimentTrackCards(summaries, portalPresentation?.tracks || []))
    // Straight to the chooser, with the URL tidied and no extra history entry.
    rememberView('tracks', { replace: true })
  }
  catch {
    // A missing or expired session keeps the normal login page visible.
  }
}

/**
 * Record which view the current history entry stands for.
 *
 * Without one entry per view, Back leaves the portal altogether — which is
 * exactly what students read as being signed out.
 *
 * @param {'tracks' | 'dashboard'} view @param {{ replace?: boolean }} [options]
 */
function rememberView(view, { replace = false } = {}) {
  if (window.history.state?.view === view)
    return
  const entry = { view }
  if (replace)
    window.history.replaceState(entry, '', '/portal/')
  else
    window.history.pushState(entry, '', '/portal/')
}

// Back and Forward move between the portal's own views.
window.addEventListener('popstate', (event) => {
  if (event.state?.view === 'dashboard')
    void showReservations({ replace: true })
  else
    void showTracks({ replace: true })
})

function campusNow() {
  return campusClock()
}

const today = campusDay(campusNow())
elements.day.min = today
elements.day.max = addCampusDays(campusNow(), 6)
elements.day.value = today

elements.loginForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = new FormData(elements.loginForm)
  setBusy(elements.loginForm, true)
  hideMessage(elements.loginError)
  try {
    const login = await api.login(String(form.get('studentNumber')).trim(), String(form.get('loginCode')))
    elements.loginForm.reset()
    elements.loginView.hidden = true
    elements.logout.hidden = false
    hideMessage(elements.message)
    await showTracks()
  }
  catch (error) {
    // On the sign-in form a 401 means the credentials were rejected, not that an
    // established session expired.
    const text = error instanceof CampusApiError && error.status === 401
      ? messages.login.badCredentials
      : messageFor(error)
    showMessage(elements.loginError, text, true)
  }
  finally {
    setBusy(elements.loginForm, false)
  }
})

elements.refreshButton.addEventListener('click', () => {
  hideMessage(elements.message)
  refreshDashboard()
})
elements.day.addEventListener('change', loadSlots)
elements.reservationPrevious.addEventListener('click', () => {
  reservationPage -= 1
  renderReservationHistory()
})
elements.reservationNext.addEventListener('click', () => {
  reservationPage += 1
  renderReservationHistory()
})
elements.launchButton.addEventListener('click', async () => {
  setBusy(elements.launchButton, true)
  hideMessage(elements.message)
  try {
    await launchWorkspace(
      () => api.launchSession(),
      path => window.location.assign(path),
    )
  }
  catch (error) {
    showMessage(elements.message, messageFor(error), true)
  }
  finally {
    setBusy(elements.launchButton, false)
  }
})

elements.slotList.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-starts-at]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  setBusy(button, true)
  try {
    const reservation = await api.reserve(button.getAttribute('data-starts-at') || '')
    const bookingMessage = reservation.status === 'waitlisted'
      ? messages.booking.waitlisted
      : isCurrentAccessSlot(reservation, campusNow())
        ? messages.booking.supplemented
        : messages.booking.confirmed
    await refreshDashboard()
    showMessage(elements.message, bookingMessage)
  }
  catch (error) {
    showMessage(elements.message, messageFor(error), true)
  }
  finally {
    setBusy(button, false)
  }
})

elements.reservationList.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('[data-cancel-id]') : null
  if (!(button instanceof HTMLButtonElement))
    return
  const reservationId = button.getAttribute('data-cancel-id') || ''
  const reservation = lastReservations.find(item => item.id === reservationId)
  const inProgress = reservation !== undefined && isCurrentAccessSlot(reservation, campusNow())
  const prompt = inProgress ? messages.reservations.confirmCancelInProgress : messages.reservations.confirmCancel
  if (!window.confirm(prompt))
    return
  setBusy(button, true)
  try {
    await api.cancelReservation(reservationId)
    await refreshDashboard()
    showMessage(elements.message, inProgress ? messages.booking.cancelledInProgress : messages.booking.cancelled)
  }
  catch (error) {
    showMessage(elements.message, messageFor(error), true)
  }
  finally {
    setBusy(button, false)
  }
})

elements.passwordForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = new FormData(elements.passwordForm)
  setBusy(elements.passwordForm, true)
  hideMessage(elements.passwordMessage)
  try {
    await api.changePassword(String(form.get('currentPassword')), String(form.get('newPassword')))
    elements.passwordForm.reset()
    showMessage(elements.passwordMessage, messages.password.changed)
  }
  catch (error) {
    showMessage(elements.passwordMessage, passwordErrorMessage(error), true)
  }
  finally {
    setBusy(elements.passwordForm, false)
  }
})

setInterval(passiveRefresh, POLL_INTERVAL_MS)
window.addEventListener('focus', passiveRefresh)
document.addEventListener('visibilitychange', () => {
  if (!document.hidden)
    passiveRefresh()
})
window.addEventListener('resize', () => {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    if (!elements.dashboardView.hidden)
      renderReservationHistory()
  })
})

function passiveRefresh() {
  if (document.hidden || elements.dashboardView.hidden)
    return
  if (Date.now() - lastRefreshAtMs < PASSIVE_REFRESH_MIN_GAP_MS)
    return
  refreshDashboard()
}

async function refreshDashboard() {
  setBusy(elements.refreshButton, true)
  lastRefreshAtMs = Date.now()
  try {
    const dashboard = await api.dashboard()
    if (dashboard.access.server_now)
      campusClock = createCampusClock(dashboard.access.server_now, Date.now())
    const promotions = detectPromotions(lastReservations, dashboard.reservations)
    lastReservations = dashboard.reservations
    renderDashboard(dashboard)
    await loadSlots()
    if (promotions.length)
      showMessage(elements.message, messages.booking.promoted)
  }
  catch (error) {
    if (error instanceof CampusApiError && error.status === 401) {
      elements.dashboardView.hidden = true
      elements.tracksView.hidden = true
        elements.loginView.hidden = false
      showMessage(elements.loginError, messageFor(error), true)
      return
    }
    showMessage(elements.message, messageFor(error), true)
  }
  finally {
    setBusy(elements.refreshButton, false)
  }
}

async function loadSlots() {
  elements.slotList.setAttribute('aria-busy', 'true')
  elements.slotList.textContent = messages.loading.slots
  try {
    const response = await api.listSlots(elements.day.value)
    const visible = visibleSlots(response.data, campusNow())
    if (!visible.length && elements.day.value === campusDay(campusNow())) {
      elements.day.value = addCampusDays(campusNow(), 1)
      showMessage(elements.message, messages.slots.allEnded)
      elements.slotList.removeAttribute('aria-busy')
      return loadSlots()
    }
    elements.slotList.replaceChildren(...visible.map(slotCard))
  }
  catch (error) {
    elements.slotList.textContent = messageFor(error)
  }
  finally {
    elements.slotList.removeAttribute('aria-busy')
  }
}

/** @param {import('./campus-api.js').Dashboard} dashboard */
function renderDashboard({ access, reservations, allowance }) {
  // An exhausted allowance closes only the Dify workspace. The reservation
  // centre, the manuals and signing in stay open, so this disables the launch
  // button rather than the page, and the reason is spelled out on the button.
  const exhausted = !allowance.model_calls_enabled
  elements.accessState.textContent = access.allowed ? '可进入' : '未开放'
  elements.accessDetail.textContent = access.allowed && access.ends_at ? `访问权限至 ${formatTime(access.ends_at)}` : '需在已确认的预约时段内进入'
  elements.launchButton.disabled = !access.allowed || exhausted
  elements.launchButton.title = exhausted ? '模型额度已用尽，无法进入 Dify 工作区' : ''
  // Amounts are stored with four decimals; students read them in 元 with two.
  elements.allowanceRemaining.textContent = Number(allowance.remaining_usd).toFixed(2)
  elements.allowanceDetail.textContent = `累计使用 ${allowance.used_usd} 元 · ${allowance.model_calls_enabled ? '模型可用' : '模型额度已用完'}`
  const unfinished = reservations.find(item => item.status === 'confirmed' || item.status === 'waitlisted')
  elements.reservationState.textContent = unfinished ? statusLabel(unfinished.status) : '暂无'
  elements.reservationDetail.textContent = unfinished ? `${formatDateTime(unfinished.starts_at)} – ${formatTime(unfinished.ends_at)}` : messages.reservations.noneDetail
  renderCountdown(unfinished)
  renderReservationHistory()
}

function renderReservationHistory() {
  const page = paginateReservations(lastReservations, reservationPage, window.innerHeight)
  reservationPage = page.page
  elements.reservationList.replaceChildren(
    ...(page.items.length ? page.items.map(reservationCard) : [emptyReservation()]),
  )
  elements.reservationPage.textContent = `第 ${page.page + 1} / ${page.pageCount} 页 · 共 ${lastReservations.length} 条`
  elements.reservationPrevious.disabled = page.page === 0
  elements.reservationNext.disabled = page.page === page.pageCount - 1
  elements.reservationPagination.hidden = page.pageCount === 1
}

/** @param {import('./campus-api.js').Reservation | undefined} reservation */
function renderCountdown(reservation) {
  clearInterval(countdownTimer)
  if (!reservation) {
    elements.reservationCountdown.hidden = true
    return
  }
  const tick = () => {
    const timing = reservationTiming(reservation, campusNow())
    if (!timing) {
      clearInterval(countdownTimer)
      elements.reservationCountdown.hidden = true
      refreshDashboard()
      return
    }
    const label = timing.phase === 'before' ? messages.countdown.beforeStart : messages.countdown.beforeEnd
    elements.reservationCountdown.textContent = `${label} ${formatCountdown(timing.remainingMs)}`
    elements.reservationCountdown.classList.toggle(
      'urgent',
      timing.phase === 'during' && timing.remainingMs <= URGENT_COUNTDOWN_MS,
    )
    elements.reservationCountdown.hidden = false
  }
  tick()
  countdownTimer = setInterval(tick, 1_000)
}

/** @param {import('./campus-api.js').AccessSlot} slot */
function slotCard(slot) {
  const article = document.createElement('article')
  article.className = 'slot'
  if (isCurrentAccessSlot(slot, campusNow()))
    article.classList.add('current')
  const copy = document.createElement('div')
  const title = document.createElement('strong')
  title.textContent = `${formatTime(slot.starts_at)} – ${formatTime(slot.ends_at)}`
  const meta = document.createElement('small')
  meta.textContent = `已预约 ${slot.confirmed}/${slot.capacity}${slot.waitlisted ? ` · 候补 ${slot.waitlisted}` : ''}`
  copy.append(title, meta)
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'secondary compact'
  button.dataset.startsAt = slot.starts_at
  button.disabled = !slot.reservable
  button.textContent = slot.reservable
    ? slot.confirmed >= slot.capacity
      ? messages.slots.waitlist
      : isCurrentAccessSlot(slot, campusNow())
        ? messages.slots.supplement
        : messages.slots.reserve
    : slot.capacity === 0
      ? messages.slots.closed
      : messages.slots.unavailable
  article.append(copy, button)
  return article
}

/** @param {import('./campus-api.js').Reservation} reservation */
function reservationCard(reservation) {
  const article = document.createElement('article')
  article.className = 'reservation'
  if (reservation.status === 'confirmed' && isCurrentAccessSlot(reservation, campusNow()))
    article.classList.add('current')
  else if (reservation.status === 'waitlisted')
    article.classList.add('waitlisted')
  const status = document.createElement('span')
  status.className = `status ${reservation.status}`
  status.textContent = statusLabel(reservation.status)
  const time = document.createElement('strong')
  time.textContent = formatDateTime(reservation.starts_at)
  const detail = document.createElement('small')
  detail.textContent = `${formatTime(reservation.starts_at)} – ${formatTime(reservation.ends_at)}${reservation.waitlist_position ? ` · 候补第 ${reservation.waitlist_position} 位` : ''}`
  article.append(status, time, detail)
  if (canCancelReservation(reservation, campusNow())) {
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'text-button'
    button.dataset.cancelId = reservation.id
    button.textContent = messages.reservations.cancel
    article.append(button)
  }
  return article
}

function emptyReservation() {
  const empty = document.createElement('p')
  empty.className = 'empty'
  empty.textContent = messages.reservations.empty
  return empty
}

/** @param {import('./campus-api.js').ReservationStatus} status */
function statusLabel(status) {
  return messages.reservations.statuses[status] || status
}

/** @param {HTMLElement} owner @param {boolean} busy */
function setBusy(owner, busy) {
  owner.setAttribute('aria-busy', String(busy))
  owner.querySelectorAll('button, input').forEach(control => {
    if (control instanceof HTMLButtonElement || control instanceof HTMLInputElement)
      control.disabled = busy
  })
  if (owner instanceof HTMLButtonElement)
    owner.disabled = busy
}

/** @param {HTMLElement} element @param {string} message @param {boolean} [error] */
function showMessage(element, message, error = false) {
  element.textContent = message
  element.classList.toggle('error', error)
  element.hidden = false
}

/** @param {HTMLElement} element */
function hideMessage(element) {
  element.hidden = true
}

/** @param {string} value */
function formatDateTime(value) {
  return new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short', timeZone: 'Asia/Shanghai' }).format(new Date(value))
}

/** @param {string} value */
function formatTime(value) {
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Shanghai' }).format(new Date(value))
}

/**
 * @template {Element} T
 * @param {string} selector
 * @param {{ new(): T }} type
 * @returns {T}
 */
function requiredElement(selector, type) {
  const element = document.querySelector(selector)
  if (!(element instanceof type))
    throw new Error(`Missing required portal element: ${selector}`)
  return element
}

/** Show the experiment selection page and load every track. */
async function showTracks({ replace = false } = {}) {
  elements.dashboardView.hidden = true
  elements.tracksView.hidden = false
  hideMessage(elements.tracksMessage)
  elements.trackList.textContent = '正在读取实验列表…'
  rememberView('tracks', { replace })
  try {
    renderTrackCards(experimentTrackCards(await api.listExperimentTracks(), portalPresentation?.tracks || []))
  }
  catch (error) {
    elements.trackList.textContent = ''
    showMessage(elements.tracksMessage, messageFor(error), true)
  }
}

/**
 * One row per experiment. The manual titles are the administrator's own
 * chapter list, laid out in two columns, and each one is a plain link to the
 * byte-preserved HTML on the dedicated manual origin — there is no reader
 * page in the portal any more.
 *
 * @param {import('./portal-domain.js').ExperimentTrackCard[]} cards
 */
function renderTrackCards(cards) {
  if (!cards.length) {
    elements.trackList.textContent = '暂无可用实验。'
    return
  }
  const list = document.createElement('div')
  list.className = 'track-cards'
  for (const card of cards) {
    const item = document.createElement('article')
    item.className = card.manualAvailable || card.reservationsAvailable ? 'track-card' : 'track-card unavailable'
    const heading = document.createElement('h2')
    heading.textContent = card.title
    const detail = document.createElement('p')
    detail.className = 'muted'
    detail.textContent = card.detail
    item.id = `track-${card.track}`
    const actions = trackActions(card)
    // The reservation button belongs right under the description line,
    // before the chapter titles, not after them.
    item.append(heading, detail, ...(actions ? [actions] : []), chapterLinks(card))
    list.append(item)
  }
  elements.trackList.replaceChildren(list)
}

/** The chapter titles, two per row, each linking at the manual origin. */
function chapterLinks(card) {
  const grid = document.createElement('ul')
  grid.className = 'chapter-links'
  for (const chapter of card.chapters) {
    const item = document.createElement('li')
    const link = document.createElement('a')
    link.className = 'chapter-link'
    link.href = chapter.view_url
    const title = document.createElement('span')
    title.className = 'chapter-link-title'
    title.textContent = chapter.title
    link.append(title)
    // The teaser is derived from the document itself; a document without
    // readable text simply has none.
    if (chapter.summary) {
      const summary = document.createElement('span')
      summary.className = 'chapter-link-summary'
      summary.textContent = chapter.summary
      link.append(summary)
    }
    item.append(link)
    grid.append(item)
  }
  if (!card.chapters.length) {
    const empty = document.createElement('li')
    empty.className = 'chapter-empty'
    empty.textContent = card.reservationsAvailable ? '在预约中心进入 Dify 工作区' : '还没有发布内容'
    grid.append(empty)
  }
  return grid
}

/** The reservation shortcut, or null when the track has no such destination. */
function trackActions(card) {
  if (!card.reservationsAvailable)
    return null
  const actions = document.createElement('div')
  actions.className = 'row-actions'
  const action = document.createElement('button')
  action.type = 'button'
  action.className = 'primary'
  action.textContent = '进入预约中心'
  action.dataset.destination = 'reservations'
  actions.append(action)
  return actions
}

//: How long the jumped-to card's text stays bright before it eases back.
const FLASH_MS = 1600
let flashingCard = null
let flashTimer = 0

/**
 * Brighten the text inside the card that was jumped to, then let it ease back.
 *
 * Every experiment is handled the same way — heading, description and chapter
 * titles all brighten together. Re-clicking restarts the brightening instead of
 * looking inert, so the class is dropped and the layout re-read before it goes
 * back on. Only one card is ever bright at a time.
 *
 * @param {HTMLElement} card
 */
function flashTrackCard(card) {
  if (flashingCard && flashingCard !== card)
    flashingCard.classList.remove('is-flashing')
  window.clearTimeout(flashTimer)
  card.classList.remove('is-flashing')
  void card.clientWidth
  card.classList.add('is-flashing')
  flashingCard = card
  flashTimer = window.setTimeout(() => {
    card.classList.remove('is-flashing')
    if (flashingCard === card)
      flashingCard = null
  }, FLASH_MS)
}

/**
 * Centre an experiment card, which is where its name in the grey line points.
 *
 * @param {string} track
 */
function centreTrackCard(track) {
  const card = document.getElementById(`track-${track}`)
  if (card)
    card.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return card
}

// Delegated on the document: the cards are re-rendered on every refresh
// while the grey line is static markup.
document.addEventListener('click', (event) => {
  const link = event.target instanceof Element ? event.target.closest('[data-jump-track]') : null
  if (!link)
    return
  event.preventDefault()
  const card = centreTrackCard(link.getAttribute('data-jump-track') || '')
  if (card)
    flashTrackCard(card)
})

elements.trackList.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('button[data-destination]') : null
  if (!(button instanceof HTMLButtonElement) || button.disabled)
    return
  if (button.dataset.destination === 'reservations')
    await showReservations()
})

/** Sign out, drop the session state and put the login form back. */
async function signOut() {
  try {
    await api.logout()
  }
  catch {
    // An expired session still has to land on the login view.
  }
  elements.logout.hidden = true
  elements.tracksView.hidden = true
  elements.dashboardView.hidden = true
  elements.loginView.hidden = false
  elements.trackList.textContent = ''
  hideMessage(elements.message)
  window.history.replaceState(null, '', '/portal/')
}

elements.logout.addEventListener('click', () => { void signOut() })

elements.tracksRefresh.addEventListener('click', () => { void showTracks() })
elements.dashboardBack.addEventListener('click', () => { void showTracks() })

/** Show the reservation centre, which is the large-model track's destination. */
async function showReservations({ replace = false } = {}) {
  elements.tracksView.hidden = true
  elements.dashboardView.hidden = false
  hideMessage(elements.message)
  rememberView('dashboard', { replace })
  await refreshDashboard()
}

