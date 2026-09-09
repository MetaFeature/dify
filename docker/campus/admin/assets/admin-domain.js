/** Pure helpers for the Campus administration portal. */

const CAMPUS_TIME_ZONE = 'Asia/Shanghai'

/** @typedef {{ student_number: string, display_name: string, cohort?: string | null, password?: string | null }} RosterRow */

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

/** @param {{ ends_at: string }} slot @param {Date} serverNow */
export function slotEnded(slot, serverNow) {
  return new Date(slot.ends_at).getTime() <= serverNow.getTime()
}

const ROSTER_COLUMN_ALIASES = new Map([
  ['student_number', 'student_number'],
  ['display_name', 'display_name'],
  ['cohort', 'cohort'],
  ['password', 'password'],
  ['学号', 'student_number'],
  ['姓名', 'display_name'],
  ['班级', 'cohort'],
  ['密码', 'password'],
])

/**
 * Parse the administrator roster CSV. The first row must name its columns
 * (student_number and display_name required; cohort and password optional).
 * Fields must not contain commas or quotes — this parser is deliberately
 * simple and rejects rows with a mismatched field count.
 *
 * @param {string} text
 * @returns {{ rows: RosterRow[], errors: string[] }}
 */
export function parseRosterCsv(text) {
  /** @type {RosterRow[]} */
  const rows = []
  /** @type {string[]} */
  const errors = []
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(line => line.length)
  if (!lines.length)
    return { rows, errors: ['文件为空'] }
  const sourceHeader = lines[0].replace(/^\uFEFF/, '').split(',').map(cell => cell.trim())
  const header = sourceHeader.map(cell => ROSTER_COLUMN_ALIASES.get(cell.toLowerCase()) || null)
  if (!header.includes('student_number') || !header.includes('display_name'))
    return { rows, errors: ['表头必须包含“学号、姓名”（或 student_number、display_name）列'] }
  const unknown = sourceHeader.filter((_, index) => header[index] === null)
  if (unknown.length)
    return { rows, errors: [`无法识别的列：${unknown.join('、')}`] }
  if (new Set(header).size !== header.length)
    return { rows, errors: ['表头包含重复列'] }
  const seen = new Set()
  for (const [index, line] of lines.slice(1).entries()) {
    const cells = line.split(',').map(cell => cell.trim())
    if (cells.length !== header.length) {
      errors.push(`第 ${index + 2} 行的列数与表头不一致`)
      continue
    }
    const record = Object.fromEntries(header.map((name, cellIndex) => [name, cells[cellIndex]]))
    if (!record.student_number || !record.display_name) {
      errors.push(`第 ${index + 2} 行缺少学号或姓名`)
      continue
    }
    if (seen.has(record.student_number)) {
      errors.push(`第 ${index + 2} 行学号 ${record.student_number} 重复`)
      continue
    }
    seen.add(record.student_number)
    rows.push({
      student_number: record.student_number,
      display_name: record.display_name,
      cohort: record.cohort || null,
      password: record.password || null,
    })
  }
  return { rows, errors }
}

/**
 * Shape roster rows into the sync payload, dropping empty optional fields.
 *
 * @param {RosterRow[]} rows
 */
export function rosterPayload(rows) {
  return {
    students: rows.map(row => ({
      student_number: row.student_number,
      display_name: row.display_name,
      ...(row.cohort ? { cohort: row.cohort } : {}),
      ...(row.password ? { password: row.password } : {}),
    })),
  }
}

/**
 * Read the CSRF double-submit cookie so requests can echo it in the header.
 *
 * @param {string} cookieHeader
 */
export function csrfTokenFromCookie(cookieHeader) {
  for (const part of cookieHeader.split(';')) {
    const [name, ...rest] = part.trim().split('=')
    if (name === 'csrf_token' || name === '__Host-csrf_token')
      return decodeURIComponent(rest.join('='))
  }
  return null
}

/**
 * Decide how the administration portal recovers from a failed authorization probe.
 *
 * A signed-in non-administrator must be offered a sign-out. Dify's `/signin`
 * bounces an already-authenticated browser straight back to the listener root,
 * which this portal owns, so a bare sign-in link traps them in a loop with no
 * way to switch account. Cookies are not port-scoped, so a student session from
 * the Access portal on another port arrives here as exactly that case.
 *
 * @param {number} status
 * @param {string | null} [accountLabel]
 * @returns {{ message: string, actionLabel: string, action: 'signin' | 'logout-then-signin' | 'reload' }}
 */
export function authFailureView(status, accountLabel = null) {
  if (status === 403) {
    return {
      message: accountLabel
        ? `当前登录的账号「${accountLabel}」不是校园平台管理员。请退出后使用管理员账号登录。`
        : '当前登录的账号不是校园平台管理员。请退出后使用管理员账号登录。',
      actionLabel: '退出并切换账号',
      action: 'logout-then-signin',
    }
  }
  if (status === 401) {
    return {
      message: '尚未登录或会话已过期，请先登录 Dify 控制台。',
      actionLabel: '前往登录',
      action: 'signin',
    }
  }
  return {
    message: '暂时无法确认管理员身份，请稍后重试。',
    actionLabel: '重试',
    action: 'reload',
  }
}
