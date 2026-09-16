import { csrfTokenFromCookie } from './admin-domain.js'

const CONSOLE_API_BASE = '/console/api/campus'

/** @typedef {{ starts_at: string, ends_at: string, capacity: number, confirmed: number, waitlisted: number, reservable: boolean }} AdminSlot */
/** @typedef {{ data: AdminSlot[], server_now: string }} AdminSlotList */
/** @typedef {{ starts_at: string, ends_at: string, capacity: number, previous_capacity: number, confirmed: number, waitlisted: number }} SlotCapacityChange */
/** @typedef {{ capacity: number, platform_default: number, configured_capacity: number | null, is_default: boolean }} SlotCapacitySetting */
/** @typedef {{ capacity: number, platform_default: number, configured_capacity: number | null, is_default: boolean, previous_configured_capacity: number | null, scanned_slots: number, changed_slots: number, promoted_waiters: number }} SlotCapacitySettingChange */
/** Amounts arrive as decimal strings, the way every other allowance response is shaped. @typedef {{ default_allowance_usd: string, platform_default_usd: string, configured_default_allowance_usd: string | null, is_default: boolean }} DefaultAllowanceSetting */
/** @typedef {{ default_allowance_usd: string, platform_default_usd: string, configured_default_allowance_usd: string | null, is_default: boolean, previous_configured_default_allowance_usd: string | null, scanned_students: number, changed_students: number }} DefaultAllowanceSettingChange */
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

  /** @returns {Promise<SlotCapacitySetting>} */
  async knowledgeLimitSetting() {
    return this.#request('/admin/knowledge-limits')
  }

  /** @param {{ max_datasets_per_workspace: number, max_documents_per_dataset: number }} payload */
  async setKnowledgeLimit(payload) {
    return this.#request('/admin/knowledge-limits', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  }

  async restoreKnowledgeLimit() {
    return this.#request('/admin/knowledge-limits', { method: 'DELETE' })
  }

  /** @returns {Promise<DefaultAllowanceSetting>} */
  async defaultAllowanceSetting() {
    return this.#request('/admin/default-allowance')
  }

  /** @param {number} defaultAllowanceUsd @returns {Promise<DefaultAllowanceSettingChange>} */
  async setDefaultAllowance(defaultAllowanceUsd) {
    return this.#request('/admin/default-allowance', {
      method: 'PUT',
      body: JSON.stringify({ default_allowance_usd: defaultAllowanceUsd }),
    })
  }

  /** @returns {Promise<DefaultAllowanceSettingChange>} */
  async restoreDefaultAllowance() {
    return this.#request('/admin/default-allowance', { method: 'DELETE' })
  }

  async slotCapacitySetting() {
    return this.#request('/admin/slot-capacity')
  }

  /** @param {number} capacity @returns {Promise<SlotCapacitySettingChange>} */
  async setSlotCapacityDefault(capacity) {
    return this.#request('/admin/slot-capacity', {
      method: 'PUT',
      body: JSON.stringify({ capacity }),
    })
  }

  /** @returns {Promise<SlotCapacitySettingChange>} */
  async restoreSlotCapacityDefault() {
    return this.#request('/admin/slot-capacity', { method: 'DELETE' })
  }

  /**
   * @param {number} limit @param {number} offset
   * @param {string} [keyword] matched against the student number or name
   * @returns {Promise<{ data: AdminStudent[] }>}
   */
  /**
   * @param {number} limit @param {number} offset
   * @param {{ keyword?: string, cohort?: string, deletedOnly?: boolean }} [filters]
   */
  async listStudents(limit, offset, filters = {}) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    const needle = (filters.keyword ?? '').trim()
    if (needle)
      params.set('keyword', needle)
    const cohort = (filters.cohort ?? '').trim()
    if (cohort)
      params.set('cohort', cohort)
    if (filters.deletedOnly)
      params.set('deleted_only', 'true')
    return this.#request(`/admin/students?${params}`)
  }

  /** @returns {Promise<{ data: string[] }>} */
  async studentCohorts() {
    return this.#request('/admin/students/cohorts')
  }

  /** @returns {Promise<{ students: number, ready: number }>} */
  async provisioningProgress() {
    return this.#request('/admin/students/provisioning-progress')
  }


  /** @param {string} studentNumber @returns {Promise<AdminStudent>} */
  async deleteStudent(studentNumber) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}`, { method: 'DELETE' })
  }

  /** @param {string} studentNumber @returns {Promise<AdminStudent>} */
  async restoreStudent(studentNumber) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/restore`, { method: 'POST' })
  }

  /** @param {string} studentNumber @param {string} displayName @returns {Promise<AdminStudent>} */
  async renameStudent(studentNumber, displayName) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/name`, {
      method: 'PUT',
      body: JSON.stringify({ display_name: displayName }),
    })
  }

  /** Permanently purge students soft-deleted longer ago than the retention window. */
  async purgeExpiredStudents() {
    return this.#request('/admin/students/purge', { method: 'POST' })
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
  /** @returns {Promise<{ student_number: string, password: string }>} */
  async resetStudentPassword(studentNumber) {
    return this.#request(`/admin/students/${encodeURIComponent(studentNumber)}/password`, { method: 'PUT' })
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
  async portalLoginState() {
    return this.#request('/admin/portal-login')
  }

  /** @param {File} file @returns {Promise<unknown>} */
  async uploadPortalLoginPage(file) {
    const body = new FormData()
    body.append('file', file)
    return this.#request('/admin/portal-login', { method: 'POST', body })
  }

  /** @param {string} pageId @returns {Promise<unknown>} */
  async activatePortalLoginPage(pageId) {
    return this.#request(
      `/admin/portal-login/pages/${encodeURIComponent(pageId)}/activate`,
      { method: 'POST' },
    )
  }

  /** @returns {Promise<unknown>} */
  async restoreBuiltInPortalLogin() {
    return this.#request('/admin/portal-login', { method: 'DELETE' })
  }

  /** @param {string} pageId @returns {Promise<unknown>} */
  async deletePortalLoginPage(pageId) {
    return this.#request(`/admin/portal-login/pages/${encodeURIComponent(pageId)}`, { method: 'DELETE' })
  }

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
   * The printable usage report for one granularity.
   *
   * The page has to travel through XHR rather than a plain link: the console
   * only accepts the CSRF token from the X-CSRF-Token header, so opening the
   * endpoint in a tab can never authenticate (see CODEBUDDY §31).
   *
   * @param {string} granularity
   * @returns {Promise<{ blob: Blob, filename: string | null }>}
   */
  async usageReportPage(granularity) {
    return this.#fetchBlob(`/admin/usage-report?granularity=${encodeURIComponent(granularity)}`)
  }

  /**
   * The same three tables as a workbook, ready to save.
   *
   * @param {string} granularity
   * @returns {Promise<{ blob: Blob, filename: string | null }>}
   */
  async usageReportWorkbook(granularity) {
    return this.#fetchBlob(`/admin/usage-report.xlsx?granularity=${encodeURIComponent(granularity)}`)
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
   * Fetch a file rather than JSON, with the same session and CSRF handling.
   *
   * @param {string} path
   * @param {RequestInit} [options]
   * @returns {Promise<{ blob: Blob, filename: string | null }>}
   */
  async #fetchBlob(path, options = {}) {
    const headers = new Headers(options.headers)
    const csrfToken = csrfTokenFromCookie(this.cookieSource())
    if (csrfToken)
      headers.set('X-CSRF-Token', csrfToken)
    let response
    try {
      response = await this.fetcher(`${CONSOLE_API_BASE}${path}`, {
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
    return { blob: await response.blob(), filename: filenameFrom(response) }
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
    // type and body are left alone; everything else this client sends is
    // JSON, and a caller that hands over a plain object would otherwise be
    // coerced to the literal "[object Object]" while the header still claims
    // JSON — which the server can only reject with a bare 400.
    const body = options.body && typeof options.body === 'object' && !(options.body instanceof FormData)
      ? JSON.stringify(options.body)
      : options.body
    if (body && !(options.body instanceof FormData))
      headers.set('content-type', 'application/json')
    const csrfToken = csrfTokenFromCookie(this.cookieSource())
    if (csrfToken)
      headers.set('X-CSRF-Token', csrfToken)
    let response
    try {
      response = await this.fetcher(url, {
        ...options,
        body,
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

/**
 * The server names the download, so the saved file matches the granularity.
 *
 * @param {Response} response
 * @returns {string | null}
 */
function filenameFrom(response) {
  const disposition = response.headers.get('content-disposition') ?? ''
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  return match ? decodeURIComponent(match[1]) : null
}
