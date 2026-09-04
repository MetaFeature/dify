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
  tracksView: requiredElement('#tracks-view', HTMLElement),
  manualView: requiredElement('#manual-view', HTMLElement),
  dashboardView: requiredElement('#dashboard-view', HTMLElement),
  trackList: requiredElement('#track-list', HTMLElement),
  tracksMessage: requiredElement('#tracks-message', HTMLElement),
  tracksRefresh: requiredElement('#tracks-refresh', HTMLButtonElement),
  manualTitle: requiredElement('#manual-title', HTMLElement),
  manualChapters: requiredElement('#manual-chapters', HTMLElement),
  manualBody: requiredElement('#manual-body', HTMLElement),
  manualBack: requiredElement('#manual-back', HTMLButtonElement),
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
    await api.login(String(form.get('studentNumber')).trim(), String(form.get('loginCode')))
    elements.loginForm.reset()
    elements.loginView.hidden = true
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
      elements.manualView.hidden = true
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
  elements.accessState.textContent = access.allowed ? '可进入' : '未开放'
  elements.accessDetail.textContent = access.allowed && access.ends_at ? `访问权限至 ${formatTime(access.ends_at)}` : '需在已确认的预约时段内进入'
  elements.launchButton.disabled = !access.allowed
  elements.allowanceRemaining.textContent = allowance.remaining_usd
  elements.allowanceDetail.textContent = `累计使用 $${allowance.used_usd} · ${allowance.model_calls_enabled ? '模型可用' : '模型额度已用完'}`
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

/** Show the experiment selection page and load the three tracks. */
async function showTracks() {
  elements.manualView.hidden = true
  elements.dashboardView.hidden = true
  elements.tracksView.hidden = false
  hideMessage(elements.tracksMessage)
  elements.trackList.textContent = '正在读取实验列表…'
  try {
    renderTrackCards(experimentTrackCards(await api.listExperimentTracks()))
  }
  catch (error) {
    elements.trackList.textContent = ''
    showMessage(elements.tracksMessage, messageFor(error), true)
  }
}

/** @param {import('./portal-domain.js').ExperimentTrackCard[]} cards */
function renderTrackCards(cards) {
  if (!cards.length) {
    elements.trackList.textContent = '暂无可用实验。'
    return
  }
  const list = document.createElement('div')
  list.className = 'track-cards'
  for (const card of cards) {
    const item = document.createElement('article')
    item.className = card.available ? 'track-card' : 'track-card unavailable'
    const heading = document.createElement('h2')
    heading.textContent = card.title
    const detail = document.createElement('p')
    detail.className = 'muted'
    detail.textContent = card.detail
    const action = document.createElement('button')
    action.type = 'button'
    action.className = 'primary'
    action.textContent = card.destination === 'reservations' ? '进入预约中心' : '阅读实验手册'
    action.disabled = !card.available
    action.dataset.track = card.track
    action.dataset.destination = card.destination
    item.append(heading, detail, action)
    list.append(item)
  }
  elements.trackList.replaceChildren(list)
}

elements.trackList.addEventListener('click', async (event) => {
  const button = event.target instanceof Element ? event.target.closest('button[data-destination]') : null
  if (!(button instanceof HTMLButtonElement) || button.disabled)
    return
  if (button.dataset.destination === 'reservations') {
    await showReservations()
    return
  }
  await showManual(button.dataset.track || '', button.closest('.track-card')?.querySelector('h2')?.textContent || '实验手册')
})

elements.tracksRefresh.addEventListener('click', () => { void showTracks() })
elements.manualBack.addEventListener('click', () => { void showTracks() })
elements.dashboardBack.addEventListener('click', () => { void showTracks() })

/** Show the reservation centre, which is the large-model track's destination. */
async function showReservations() {
  elements.tracksView.hidden = true
  elements.manualView.hidden = true
  elements.dashboardView.hidden = false
  hideMessage(elements.message)
  await refreshDashboard()
}

/**
 * Show one track's manual.
 *
 * The chapter body arrives already sanitized -- it is cleaned once, when an
 * administrator uploads it -- so it is assigned as HTML on purpose. Titles come
 * from the same table and are assigned as text, because they never need markup.
 *
 * @param {string} track
 * @param {string} title
 */
async function showManual(track, title) {
  elements.tracksView.hidden = true
  elements.dashboardView.hidden = true
  elements.manualView.hidden = false
  elements.manualTitle.textContent = title
  elements.manualChapters.textContent = ''
  elements.manualBody.textContent = '正在读取实验手册…'
  let chapters
  try {
    chapters = await api.labManual(track)
  }
  catch (error) {
    elements.manualBody.textContent = messageFor(error)
    return
  }
  if (!chapters.length) {
    elements.manualBody.textContent = '这本实验手册还没有发布章节。'
    return
  }
  const nav = document.createElement('ol')
  nav.className = 'chapter-list'
  for (const chapter of chapters) {
    const item = document.createElement('li')
    const link = document.createElement('button')
    link.type = 'button'
    link.className = 'chapter-link'
    link.textContent = chapter.title
    link.dataset.chapterId = chapter.id
    item.append(link)
    nav.append(item)
  }
  elements.manualChapters.replaceChildren(nav)
  renderChapter(chapters, chapters[0].id)
  elements.manualChapters.onclick = (event) => {
    const link = event.target instanceof Element ? event.target.closest('button[data-chapter-id]') : null
    if (link instanceof HTMLButtonElement)
      renderChapter(chapters, link.dataset.chapterId || '')
  }
}

/**
 * @param {import('./campus-api.js').ManualChapter[]} chapters
 * @param {string} chapterId
 */
function renderChapter(chapters, chapterId) {
  const chapter = chapters.find(candidate => candidate.id === chapterId)
  if (!chapter)
    return
  for (const link of elements.manualChapters.querySelectorAll('button[data-chapter-id]'))
    link.classList.toggle('active', link.getAttribute('data-chapter-id') === chapterId)
  const heading = document.createElement('h2')
  heading.textContent = chapter.title
  const body = document.createElement('div')
  body.className = 'chapter-body'
  // Sanitized at upload; see services/campus/lab_manual_html.py.
  body.innerHTML = chapter.body_html
  elements.manualBody.replaceChildren(heading, body)
}
