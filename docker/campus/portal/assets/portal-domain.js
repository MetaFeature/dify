const CAMPUS_TIME_ZONE = 'Asia/Shanghai'

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
/** @typedef {{ track: string, title: string, destination: 'reservations' | 'manual', detail: string, available: boolean }} ExperimentTrackCard */

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
export function experimentTrackCards(tracks) {
  /** @type {ExperimentTrackCard[]} */
  const cards = []
  for (const summary of tracks) {
    const title = TRACK_TITLES[summary.track]
    if (!title)
      continue
    if (summary.kind === 'manual') {
      const published = summary.chapters > 0
      cards.push({
        track: summary.track,
        title,
        destination: 'manual',
        detail: published ? `共 ${summary.chapters} 章 · 在自己的电脑上完成` : '实验手册尚未发布',
        available: published,
      })
      continue
    }
    cards.push({
      track: summary.track,
      title,
      destination: 'reservations',
      detail: '在 Dify 工作区完成，需先预约时段',
      available: true,
    })
  }
  return cards
}
