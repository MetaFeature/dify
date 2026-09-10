import { csrfTokenFromCookie } from './admin-domain.js'

const CONSOLE_API_BASE = '/console/api/campus'

/** @typedef {{ starts_at: string, ends_at: string, capacity: number, confirmed: number, waitlisted: number, reservable: boolean }} AdminSlot */
/** @typedef {{ data: AdminSlot[], server_now: string }} AdminSlotList */
/** @typedef {{ starts_at: string, ends_at: string, capacity: number, previous_capacity: number, confirmed: number, waitlisted: number }} SlotCapacityChange */
/** @typedef {{ id: string, student_number: string, display_name: string, cohort: string | null, status: string, has_credential?: boolean | null, virtual_identity?: boolean | null }} AdminStudent */
/** @typedef {{ created: number, updated: number, password_resets: number }} SyncOutcome */
/** @typedef {{ account_id: string, display_name: string }} Administrator */
/** @typedef {{ id: string, track: string, title: string, original_filename: string, size_bytes: number, content_url: string, position: number, status: 'draft' | 'published' }} ManualChapter */
/** @typedef {ManualChapter} SavedManualChapter */

export class AdminApiError extends Error {
  /**
   * @param {number} status
   * @param {string} message
   */
  constructor(status, message) {
    super(message)
    this.name = 'AdminApiError'
    this.status = status
  }
}

export class AdminApi {
  /**
   * @param {typeof fetch} [fetcher]
   * @param {() => string} [cookieSource]
   */
  constructor(fetcher = globalThis.fetch.bind(globalThis), cookieSource = () => document.cookie) {
    this.fetcher = fetcher
    this.cookieSource = cookieSource
  }

  /** @param {string} day @returns {Promise<AdminSlotList>} */
  async listSlots(day) {
    return this.#request(`/admin/slots?day=${encodeURIComponent(day)}`)
  }

  /** @param {string} startsAt @param {number} capacity @returns {Promise<SlotCapacityChange>} */
  async setSlotCapacity(startsAt, capacity) {
    return this.#request('/admin/slots', {
      method: 'PUT',
      body: JSON.stringify({ starts_at: startsAt, capacity }),
    })
  }

  /** @param {number} limit @param {number} offset @returns {Promise<{ data: AdminStudent[] }>} */
  async listStudents(limit, offset) {
    return this.#request(`/admin/students?limit=${limit}&offset=${offset}`)
  }

  /**
   * @param {{ student_number: string, display_name: string, cohort?: string, password: string }} payload
   * @returns {Promise<AdminStudent>}
   */
  async createStudent(payload) {
    return this.#request('/admin/students', { method: 'POST', body: JSON.stringify(payload) })
  }

  /** @param {string} studentNumber @returns {Promise<AdminStudent & { allowance: unknown }>} */
  async studentDetail(studentNumber) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}`)
  }

  /** @param {string} studentNumber @param {'active' | 'suspended'} status @returns {Promise<AdminStudent>} */
  async setStudentStatus(studentNumber, status) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    })
  }

  /** @param {string} studentNumber @param {string} password @returns {Promise<{ result: string }>} */
  async resetStudentPassword(studentNumber, password) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/password`, {
      method: 'PUT',
      body: JSON.stringify({ password }),
    })
  }

  /**
   * @param {string} studentNumber
   * @param {{ delta_usd: string, reason: string, request_id: string }} payload
   * @returns {Promise<unknown>}
   */
  async adjustAllowance(studentNumber, payload) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/allowance-adjustments`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  /** @param {object} payload @returns {Promise<SyncOutcome>} */
  async previewRoster(payload) {
    return this.#request('/admin/students/sync/preview', { method: 'POST', body: JSON.stringify(payload) })
  }

  /** @param {object} payload @returns {Promise<SyncOutcome>} */
  async syncRoster(payload) {
    return this.#request('/admin/students/sync', { method: 'POST', body: JSON.stringify(payload) })
  }

  /** @param {File} file @returns {Promise<{ data: import('./admin-domain.js').RosterRow[] }>} */
  async parseRosterWorkbook(file) {
    const body = new FormData()
    body.append('file', file)
    return this.#send(`${CONSOLE_API_BASE}/admin/students/import/parse`, { method: 'POST', body })
  }

  /**
   * Read the signed-in Dify console account, so an authorization failure can
   * name who is actually signed in instead of only saying access was denied.
   *
   * @returns {Promise<{ id: string, email: string, name: string }>}
   */
  async currentAccount() {
    return this.#requestConsole('/account/profile')
  }

  /** End the Dify console session so a different account can sign in. */
  async logout() {
    await this.#requestConsole('/logout', { method: 'POST' })
  }

  /** @param {string} track @returns {Promise<{ data: ManualChapter[] }>} */
  async listManualChapters(track) {
    return this.#request(`/admin/lab-manuals/${encodeURIComponent(track)}/chapters`)
  }

  /**
   * @param {string} track
   * @param {File} file
   * @returns {Promise<SavedManualChapter>}
   */
  async createManualChapter(track, file) {
    const body = new FormData()
    body.append('file', file)
    return this.#send(`${CONSOLE_API_BASE}/admin/lab-manuals/${encodeURIComponent(track)}/chapters`, {
      method: 'POST',
      body,
    })
  }

  /**
   * Upload one image for a track's manual and get the URL to reference it by.
   *
   * @param {string} track
   * @param {File} file
   * @returns {Promise<{ id: string, url: string }>}
   */
  async uploadManualImage(track, file) {
    const body = new FormData()
    body.append('file', file)
    // No content-type header: the browser must set the multipart boundary.
    return this.#send(`${CONSOLE_API_BASE}/admin/lab-manuals/${encodeURIComponent(track)}/images`, {
      method: 'POST',
      body,
    })
  }

  /**
   * @param {string} chapterId
   * @param {File} file
   * @returns {Promise<SavedManualChapter>}
   */
  async updateManualChapter(chapterId, file) {
    const body = new FormData()
    body.append('file', file)
    return this.#send(`${CONSOLE_API_BASE}/admin/lab-manuals/chapters/${encodeURIComponent(chapterId)}`, {
      method: 'PUT',
      body,
    })
  }

  /** @param {string} chapterId @param {'draft' | 'published'} status @returns {Promise<ManualChapter>} */
  async setManualChapterStatus(chapterId, status) {
    return this.#request(`/admin/lab-manuals/chapters/${encodeURIComponent(chapterId)}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    })
  }

  /** @param {string} chapterId @param {number} position @returns {Promise<{ data: ManualChapter[] }>} */
  async moveManualChapter(chapterId, position) {
    return this.#request(`/admin/lab-manuals/chapters/${encodeURIComponent(chapterId)}/position`, {
      method: 'PUT',
      body: JSON.stringify({ position }),
    })
  }

  /** @param {string} chapterId @returns {Promise<void>} */
  async deleteManualChapter(chapterId) {
    await this.#request(`/admin/lab-manuals/chapters/${encodeURIComponent(chapterId)}`, { method: 'DELETE' })
  }

  /** @returns {Promise<{ data: Administrator[] }>} */
  async listAdministrators() {
    return this.#request('/admin/administrators')
  }

  /** @param {{ name: string, email: string, password: string }} payload @returns {Promise<{ result: string }>} */
  async createAdministrator(payload) {
    return this.#request('/admin/administrators/create', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  }

  /** @param {string} accountId @returns {Promise<void>} */
  async removeAdministrator(accountId) {
    await this.#request(`/admin/administrators/${encodeURIComponent(accountId)}`, { method: 'DELETE' })
  }

  async presentationDraft() {
    return this.#request('/admin/presentation')
  }

  async savePresentationDraft(payload) {
    return this.#request('/admin/presentation', { method: 'PUT', body: JSON.stringify(payload) })
  }

  async publishPresentation() {
    return this.#request('/admin/presentation/publish', { method: 'POST' })
  }

  async restorePresentation() {
    return this.#request('/admin/presentation', { method: 'DELETE' })
  }

  /**
   * @template T
   * @param {string} path
   * @param {RequestInit} [options]
   * @returns {Promise<T>}
   */
  async #request(path, options = {}) {
    return this.#send(`${CONSOLE_API_BASE}${path}`, options)
  }

  /**
   * Call an upstream Dify console route rather than a Campus one.
   *
   * @template T
   * @param {string} path
   * @param {RequestInit} [options]
   * @returns {Promise<T>}
   */
  async #requestConsole(path, options = {}) {
    return this.#send(`/console/api${path}`, options)
  }

  /**
   * @template T
   * @param {string} url
   * @param {RequestInit} options
   * @returns {Promise<T>}
   */
  async #send(url, options) {
    const headers = new Headers(options.headers)
    // FormData must keep the boundary the browser generates, so its content
    // type is left alone; everything else this client sends is JSON.
    if (options.body && !(options.body instanceof FormData))
      headers.set('content-type', 'application/json')
    const csrfToken = csrfTokenFromCookie(this.cookieSource())
    if (csrfToken)
      headers.set('X-CSRF-Token', csrfToken)
    let response
    try {
      response = await this.fetcher(url, {
        ...options,
        headers,
        credentials: 'same-origin',
      })
    }
    catch {
      throw new AdminApiError(0, '无法连接服务，请检查网络后重试。')
    }
    if (!response.ok)
      throw new AdminApiError(response.status, await errorMessage(response))
    if (response.status === 204)
      return /** @type {T} */ (undefined)
    return /** @type {Promise<T>} */ (response.json())
  }
}

/** @param {Response} response */
async function errorMessage(response) {
  if (response.status === 401)
    return '尚未登录或会话已过期，请先登录 Dify 控制台。'
  if (response.status === 403)
    return '当前账号不是校园平台管理员。'
  try {
    const body = await response.json()
    if (body && typeof body.message === 'string' && body.message)
      return body.message
  }
  catch {
    // fall through to the generic message
  }
  return `操作失败（HTTP ${response.status}），请稍后重试。`
}
