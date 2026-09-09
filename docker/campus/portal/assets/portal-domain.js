const CAMPUS_TIME_ZONE = 'Asia/Shanghai'
const RESERVATION_PAGE_MIN = 2
const RESERVATION_PAGE_MAX = 6
const RESERVATION_PANEL_CHROME_HEIGHT = 680
const RESERVATION_CARD_ROW_HEIGHT = 130

/** @typedef {import('./campus-api.js').ReservationStatus} ReservationStatus */
/** @typedef {{ status: ReservationStatus, starts_at: string, ends_at: string }} CancellableReservation */
/** @typedef {{ starts_at: string, ends_at: string }} TimedSlot */

/**
 * Return the Campus calendar date independently of the browser time zone.
 *
 * @param {Date} date
 */
export function campusDay(date) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: CAMPUS_TIME_ZONE,
  }).formatToParts(date)
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

/** @param {Date} date @param {number} days */
export function addCampusDays(date, days) {
  return campusDay(new Date(date.getTime() + days * 86_400_000))
}

/** @param {CancellableReservation} reservation @param {Date} now */
export function canCancelReservation(reservation, now) {
  const unfinished = reservation.status === 'confirmed' || reservation.status === 'waitlisted'
  return unfinished && new Date(reservation.ends_at).getTime() > now.getTime()
}

/** @param {TimedSlot} slot @param {Date} now */
export function isCurrentAccessSlot(slot, now) {
  const startsAt = new Date(slot.starts_at).getTime()
  const endsAt = new Date(slot.ends_at).getTime()
  return startsAt <= now.getTime() && now.getTime() < endsAt
}

/**
 * Build a clock anchored to the server instant so a skewed browser clock
 * cannot distort slot boundaries or countdowns.
 *
 * @param {string} serverNowIso
 * @param {number} clientNowMs
 * @returns {(clientMs?: number) => Date}
 */
export function createCampusClock(serverNowIso, clientNowMs) {
  const offsetMs = new Date(serverNowIso).getTime() - clientNowMs
  return (clientMs = Date.now()) => new Date(clientMs + offsetMs)
}

/**
 * Render a remaining duration at minute granularity, switching to seconds
 * inside the final minute.
 *
 * @param {number} remainingMs
 */
export function formatCountdown(remainingMs) {
  if (remainingMs < 60_000)
    return `${Math.max(0, Math.ceil(remainingMs / 1000))} 秒`
  const minutes = Math.ceil(remainingMs / 60_000)
  const hours = Math.floor(minutes / 60)
  return hours ? `${hours} 小时 ${minutes % 60} 分钟` : `${minutes} 分钟`
}

/**
 * Report where a reservation stands relative to its fixed slot: the wait
 * before it starts, the remainder while it runs, or null once it ended.
 *
 * @param {TimedSlot} reservation @param {Date} now
 * @returns {{ phase: 'before' | 'during', remainingMs: number } | null}
 */
export function reservationTiming(reservation, now) {
  const startsAt = new Date(reservation.starts_at).getTime()
  const endsAt = new Date(reservation.ends_at).getTime()
  if (now.getTime() < startsAt)
    return { phase: 'before', remainingMs: startsAt - now.getTime() }
  if (now.getTime() < endsAt)
    return { phase: 'during', remainingMs: endsAt - now.getTime() }
  return null
}

/**
 * Return the reservations that were waitlisted at the previous refresh and
 * are confirmed now, so the portal can announce the promotion.
 *
 * @template {{ id: string, status: ReservationStatus }} T
 * @param {{ id: string, status: ReservationStatus }[]} previous
 * @param {T[]} current
 * @returns {T[]}
 */
export function detectPromotions(previous, current) {
  const waitlisted = new Set(previous.filter(item => item.status === 'waitlisted').map(item => item.id))
  return current.filter(item => item.status === 'confirmed' && waitlisted.has(item.id))
}

/**
 * Keep only slots that have not ended: ended slots offer nothing to act on.
 *
 * @template {TimedSlot} T
 * @param {T[]} slots @param {Date} now
 * @returns {T[]}
 */
export function visibleSlots(slots, now) {
  return slots.filter(slot => new Date(slot.ends_at).getTime() > now.getTime())
}

/**
 * Keep reservation history compact while using extra room on taller screens.
 * Two records remain visible on short viewports and six is the hard ceiling, so
 * a long history never stretches the booking page indefinitely.
 *
 * @param {number} viewportHeight
 */
export function reservationPageSize(viewportHeight) {
  const availableHeight = Math.max(0, viewportHeight - RESERVATION_PANEL_CHROME_HEIGHT)
  const fittingRows = Math.floor(availableHeight / RESERVATION_CARD_ROW_HEIGHT)
  return Math.min(RESERVATION_PAGE_MAX, Math.max(RESERVATION_PAGE_MIN, fittingRows))
}

/**
 * Slice reservation history into a viewport-sized page and keep the selected
 * page valid when a refresh removes records.
 *
 * @template T
 * @param {T[]} reservations
 * @param {number} requestedPage zero-based page index
 * @param {number} viewportHeight
 * @returns {{ items: T[], page: number, pageCount: number, pageSize: number }}
 */
export function paginateReservations(reservations, requestedPage, viewportHeight) {
  const pageSize = reservationPageSize(viewportHeight)
  const pageCount = Math.max(1, Math.ceil(reservations.length / pageSize))
  const normalizedPage = Number.isFinite(requestedPage) ? Math.trunc(requestedPage) : 0
  const page = Math.min(pageCount - 1, Math.max(0, normalizedPage))
  const start = page * pageSize
  return { items: reservations.slice(start, start + pageSize), page, pageCount, pageSize }
}

/**
 * Enter the guarded Dify workspace only after the backend has issued its session.
 *
 * @param {() => Promise<unknown>} launchSession
 * @param {(path: string) => void} navigate
 */
export async function launchWorkspace(launchSession, navigate) {
  await launchSession()
  navigate('/apps')
}

/** @typedef {{ track: string, kind: string, chapters: number }} ExperimentTrackSummary */
/** @typedef {{ track: string, title: string, detail: string, manualAvailable: boolean, reservationsAvailable: boolean }} ExperimentTrackCard */

/**
 * The three tracks, named the way the platform names them. The backend enum is
 * the authority for which tracks exist; this only decides how each is presented.
 */
const TRACK_TITLES = /** @type {Record<string, string>} */ ({
  'large-model': '大模型实验',
  'deep-learning': '深度学习实验',
  agent: '智能体实验',
})

/**
 * Turn the backend's track summaries into the cards the selection page renders.
 *
 * A track the portal cannot name is dropped: a card with no label is a dead end
 * for the student, and the label belongs to the portal, not the backend.
 *
 * @param {ExperimentTrackSummary[]} tracks
 * @returns {ExperimentTrackCard[]}
 */
export function experimentTrackCards(tracks, presentation = []) {
  const presented = new Map(presentation.map(item => [item.track, item]))
  /** @type {ExperimentTrackCard[]} */
  const cards = []
  for (const summary of tracks) {
    const configured = presented.get(summary.track)
    const title = configured?.title || TRACK_TITLES[summary.track]
    if (!title)
      continue
    const published = summary.chapters > 0
    cards.push({
      track: summary.track,
      title,
      detail: `${configured?.description || (summary.kind === 'dify' ? '在 Dify 工作区完成，需预约时段' : '在自己的电脑上完成')}${published ? ` · ${summary.chapters} 份课件` : ''}`,
      manualAvailable: published,
      reservationsAvailable: summary.kind === 'dify',
    })
  }
  return cards.sort((left, right) => {
    const leftPosition = presented.get(left.track)?.position || 99
    const rightPosition = presented.get(right.track)?.position || 99
    return leftPosition - rightPosition
  })
}
