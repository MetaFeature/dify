import { CampusApiError } from './campus-api.js'
import { messages } from './messages.js'

/** @param {unknown} error */
export function messageFor(error) {
  if (!(error instanceof CampusApiError))
    return messages.errors.generic
  const known = /** @type {Record<string, string>} */ (messages.errors)
  return known[error.code] || messages.errors.generic
}

/** @param {unknown} error */
export function passwordErrorMessage(error) {
  if (!(error instanceof CampusApiError))
    return messages.errors.generic
  if (error.code === 'invalid_new_password')
    return messages.password.invalidNew
  if (error.status === 403)
    return messages.password.currentWrong
  return messageFor(error)
}
