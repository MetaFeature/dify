import { CampusApi, CampusApiError } from './campus-api.js'
import { addCampusDays, campusDay, canCancelReservation } from './portal-domain.js'
import { messages } from './messages.js'

const api = new CampusApi()
const elements = {
  loginView: requiredElement('#login-view', HTMLElement),
  dashboardView: requiredElement('#dashboard-view', HTMLElement),
  loginForm: requiredElement('#login-form', HTMLFormElement),
  loginError: requiredElement('#login-error', HTMLElement),
  refreshButton: requiredElement('#refresh-button', HTMLButtonElement),
  day: requiredElement('#slot-day', HTMLInputElement),
  slotList: requiredElement('#slot-list', HTMLElement),
  reservationList: requiredElement('#reservation-list', HTMLElement),
  launchButton: requiredElement('#launch-button', HTMLButtonElement),
  message: requiredElement('#dashboard-message', HTMLElement),
  accessState: requiredElement('#access-state', HTMLElement),
  accessDetail: requiredElement('#access-detail', HTMLElement),
  allowanceRemaining: requiredElement('#allowance-remaining', HTMLElement),
  allowanceDetail: requiredElement('#allowance-detail', HTMLElement),
  reservationState: requiredElement('#reservation-state', HTMLElement),
  reservationDetail: requiredElement('#reservation-detail', HTMLElement),
}

const now = new Date()
const today = campusDay(now)
elements.day.min = today
elements.day.max = addCampusDays(now, 6)
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
    elements.dashboardView.hidden = false
    await refreshDashboard()
  }
  catch (error) {
    showMessage(elements.loginError, messageFor(error), true)
  }
  finally {
    setBusy(elements.loginForm, false)
  }
})

elements.refreshButton.addEventListener('click', refreshDashboard)
elements.day.addEventListener('change', loadSlots)
elements.launchButton.addEventListener('click', async () => {
  setBusy(elements.launchButton, true)
  hideMessage(elements.message)
  try {
    await api.launchSession()
    window.location.assign('/')
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
    showMessage(elements.message, reservation.status === 'waitlisted' ? messages.booking.waitlisted : messages.booking.confirmed)
    await refreshDashboard()
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
  setBusy(button, true)
  try {
    await api.cancelReservation(button.getAttribute('data-cancel-id') || '')
    showMessage(elements.message, messages.booking.cancelled)
    await refreshDashboard()
  }
  catch (error) {
    showMessage(elements.message, messageFor(error), true)
  }
  finally {
    setBusy(button, false)
  }
})

async function refreshDashboard() {
  setBusy(elements.refreshButton, true)
  hideMessage(elements.message)
  try {
    const dashboard = await api.dashboard()
    renderDashboard(dashboard)
    await loadSlots()
  }
  catch (error) {
    if (error instanceof CampusApiError && error.status === 401) {
      elements.dashboardView.hidden = true
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
    elements.slotList.replaceChildren(...response.data.map(slotCard))
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
  elements.allowanceRemaining.textContent = allowance.remaining_yuan
  elements.allowanceDetail.textContent = `累计使用 ¥${allowance.used_yuan} · ${allowance.model_calls_enabled ? '模型可用' : '模型额度已用完'}`
  const unfinished = reservations.find(item => item.status === 'confirmed' || item.status === 'waitlisted')
  elements.reservationState.textContent = unfinished ? statusLabel(unfinished.status) : '暂无'
  elements.reservationDetail.textContent = unfinished ? `${formatDateTime(unfinished.starts_at)} – ${formatTime(unfinished.ends_at)}` : messages.reservations.noneDetail
  elements.reservationList.replaceChildren(...(reservations.length ? reservations.map(reservationCard) : [emptyReservation()]))
}

/** @param {import('./campus-api.js').AccessSlot} slot */
function slotCard(slot) {
  const article = document.createElement('article')
  article.className = 'slot'
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
  button.textContent = slot.reservable ? (slot.confirmed >= slot.capacity ? messages.slots.waitlist : messages.slots.reserve) : messages.slots.unavailable
  article.append(copy, button)
  return article
}

/** @param {import('./campus-api.js').Reservation} reservation */
function reservationCard(reservation) {
  const article = document.createElement('article')
  article.className = 'reservation'
  const status = document.createElement('span')
  status.className = `status ${reservation.status}`
  status.textContent = statusLabel(reservation.status)
  const time = document.createElement('strong')
  time.textContent = formatDateTime(reservation.starts_at)
  const detail = document.createElement('small')
  detail.textContent = `${formatTime(reservation.starts_at)} – ${formatTime(reservation.ends_at)}${reservation.waitlist_position ? ` · 候补第 ${reservation.waitlist_position} 位` : ''}`
  article.append(status, time, detail)
  if (canCancelReservation(reservation, new Date())) {
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

/** @param {unknown} error */
function messageFor(error) {
  return error instanceof CampusApiError ? messages.errors[error.code] || messages.errors.generic : messages.errors.generic
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
