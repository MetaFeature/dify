const CAMPUS_TIME_ZONE = 'Asia/Shanghai'

/** @typedef {import('./campus-api.js').ReservationStatus} ReservationStatus */
/** @typedef {{ status: ReservationStatus, starts_at: string }} CancellableReservation */

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
  return unfinished && new Date(reservation.starts_at).getTime() > now.getTime()
}
