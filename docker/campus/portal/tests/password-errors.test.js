import assert from 'node:assert/strict'
import test from 'node:test'

import { CampusApiError } from '../assets/campus-api.js'
import { messages } from '../assets/messages.js'
import { passwordErrorMessage } from '../assets/error-messages.js'

test('password errors render only the matching safe guidance', () => {
  assert.equal(
    passwordErrorMessage(new CampusApiError(400, 'invalid_new_password')),
    messages.password.invalidNew,
  )
  assert.equal(
    passwordErrorMessage(new CampusApiError(403, 'forbidden')),
    messages.password.currentWrong,
  )
  assert.equal(
    passwordErrorMessage(new CampusApiError(400, 'bad-request')),
    messages.errors['bad-request'],
  )
})
