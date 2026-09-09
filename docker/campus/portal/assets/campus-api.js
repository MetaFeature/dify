const CAMPUS_API_BASE = '/console/api/campus'

/** @typedef {{ student_id: string, expires_at: string, must_change_password: boolean }} PortalLogin */
/** @typedef {'confirmed' | 'waitlisted' | 'cancelled' | 'completed' | 'expired'} ReservationStatus */
/** @typedef {{ id: string, status: ReservationStatus, starts_at: string, ends_at: string, waitlist_position?: number | null }} Reservation */
/** @typedef {{ starts_at: string, ends_at: string, capacity: number, confirmed: number, waitlisted: number, reservable: boolean }} AccessSlot */
/** @typedef {{ allowed: boolean, reservation_id: string | null, ends_at: string | null, server_now?: string | null }} AccessDecision */
/** @typedef {{ model: string, used_usd: string, requests: number }} ModelUsage */
/** @typedef {{ remaining_usd: string, used_usd: string, total_usd: string, model_calls_enabled: boolean, by_model: ModelUsage[] }} Allowance */
/** @typedef {{ access: AccessDecision, reservations: Reservation[], allowance: Allowance }} Dashboard */
/** @typedef {{ track: string, kind: string, chapters: number }} ExperimentTrackSummary */
/** @typedef {{ id: string, track: string, title: string, position: number, status: string, body_html: string }} ManualChapter */

/** Backend-issued safe codes the portal understands beyond plain HTTP statuses. */
const BODY_ERROR_CODES = new Set([
  'pending_reservation_exists',
  'duplicate_slot_claim',
  'invalid_new_password',
])

export class CampusApiError extends Error {
  /**
   * @param {number} status
   * @param {string} code
   */
  constructor(status, code) {
    super(code)
    this.name = 'CampusApiError'
    this.status = status
    this.code = code
  }
}

export class CampusApi {
  /** @param {typeof fetch} fetcher */
  constructor(fetcher = globalThis.fetch.bind(globalThis)) {
    this.fetcher = fetcher
  }

  /**
   * @param {string} subject
   * @param {string} credential
   * @returns {Promise<PortalLogin>}
   */
  async login(subject, credential) {
    return this.#request('/auth/virtual', {
      method: 'POST',
      body: JSON.stringify({ subject, credential }),
    })
  }

  /** @param {string} day @returns {Promise<{ data: AccessSlot[] }>} */
  async listSlots(day) {
    return this.#request(`/slots?day=${encodeURIComponent(day)}`)
  }

  /** @returns {Promise<Reservation[]>} */
  async listReservations() {
    const response = await this.#request('/reservations')
    return response.data
  }

  /** @returns {Promise<AccessDecision>} */
  async accessDecision() {
    return this.#request('/access')
  }

  /** @returns {Promise<Allowance>} */
  async allowance() {
    return this.#request('/allowance')
  }

  /** @returns {Promise<Dashboard>} */
  async dashboard() {
    const [access, reservations, allowance] = await Promise.all([
      this.accessDecision(),
      this.listReservations(),
      this.allowance(),
    ])
    return { access, reservations, allowance }
  }

  /** @param {string} startsAt @returns {Promise<Reservation>} */
  async reserve(startsAt) {
    return this.#request('/reservations', {
      method: 'POST',
      body: JSON.stringify({ starts_at: startsAt }),
    })
  }

  /** @param {string} reservationId @returns {Promise<void>} */
  async cancelReservation(reservationId) {
    await this.#request(`/reservations/${encodeURIComponent(reservationId)}`, {
      method: 'DELETE',
    })
  }

  /** @returns {Promise<ExperimentTrackSummary[]>} */
  async listExperimentTracks() {
    const response = await this.#request('/experiment-tracks')
    return response.data
  }

  async presentation() {
    return this.#request('/presentation')
  }

  /**
   * Read one track's published lab manual.
   *
   * @param {string} track
   * @returns {Promise<ManualChapter[]>}
   */
  async labManual(track) {
    const response = await this.#request(`/lab-manuals/${encodeURIComponent(track)}`)
    return response.data
  }

  /** @returns {Promise<{ result: string }>} */
  async launchSession() {
    return this.#request('/session/launch', { method: 'POST' })
  }

  /**
   * @param {string} currentPassword
   * @param {string} newPassword
   * @returns {Promise<{ result: string }>}
   */
  async changePassword(currentPassword, newPassword) {
    return this.#request('/password', {
      method: 'POST',
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    })
  }

  /**
   * @template T
   * @param {string} path
   * @param {RequestInit} [options]
   * @returns {Promise<T>}
   */
  async #request(path, options = {}) {
    const headers = new Headers(options.headers)
    if (options.body)
      headers.set('content-type', 'application/json')
    let response
    try {
      response = await this.fetcher(`${CAMPUS_API_BASE}${path}`, {
        ...options,
        headers,
        credentials: 'same-origin',
      })
    }
    catch {
      throw new CampusApiError(0, 'network')
    }
    if (!response.ok)
      throw new CampusApiError(response.status, await bodyErrorCode(response) || errorCode(response.status))
    if (response.status === 204)
      return /** @type {T} */ (undefined)
    return /** @type {Promise<T>} */ (response.json())
  }
}

/**
 * Read a known backend error code from the response body, if any.
 *
 * @param {Response} response
 * @returns {Promise<string | null>}
 */
async function bodyErrorCode(response) {
  try {
    const body = await response.json()
    return body && typeof body.code === 'string' && BODY_ERROR_CODES.has(body.code) ? body.code : null
  }
  catch {
    return null
  }
}

/**
 * @param {number} status
 * @returns {'bad-request' | 'unauthorized' | 'forbidden' | 'not-found' | 'conflict' | 'busy' | 'unavailable'}
 */
function errorCode(status) {
  if (status === 400)
    return 'bad-request'
  if (status === 401)
    return 'unauthorized'
  if (status === 403)
    return 'forbidden'
  if (status === 404)
    return 'not-found'
  if (status === 409)
    return 'conflict'
  if (status === 429)
    return 'busy'
  return 'unavailable'
}
