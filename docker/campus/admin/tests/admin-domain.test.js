import assert from 'node:assert/strict'
import test from 'node:test'

import { authFailureView, campusDay, csrfTokenFromCookie, parseRosterCsv, rosterPayload, slotEnded } from '../assets/admin-domain.js'

test('campus day follows UTC+8 regardless of the local clock', () => {
  assert.equal(campusDay(new Date('2026-08-11T16:30:00Z')), '2026-08-12')
})

test('slot end comparison uses the server instant', () => {
  const slot = { ends_at: '2026-08-12T04:00:00Z' }

  assert.equal(slotEnded(slot, new Date('2026-08-12T03:59:59Z')), false)
  assert.equal(slotEnded(slot, new Date('2026-08-12T04:00:00Z')), true)
})

test('roster CSV requires the identifying columns and rejects unknown ones', () => {
  assert.deepEqual(parseRosterCsv('').errors, ['文件为空'])
  assert.match(parseRosterCsv('name,pwd\n1,2').errors[0], /表头/)
  assert.match(parseRosterCsv('student_number,display_name,grade\n1,王,3').errors[0], /无法识别/)
})

test('roster CSV parses optional columns and reports bad rows with line numbers', () => {
  const { rows, errors } = parseRosterCsv([
    'student_number,display_name,cohort,password',
    '20260001,王一,2026-A,Ngc0001',
    '20260002,李二,,',
    '20260002,李二重复,,',
    ',缺学号,,x',
    '20260003,赵三,2026-B',
  ].join('\n'))

  assert.deepEqual(rows, [
    { student_number: '20260001', display_name: '王一', cohort: '2026-A', password: 'Ngc0001' },
    { student_number: '20260002', display_name: '李二', cohort: null, password: null },
  ])
  assert.equal(errors.length, 3)
  assert.match(errors[0], /第 4 行学号 20260002 重复/)
  assert.match(errors[1], /第 5 行缺少学号或姓名/)
  assert.match(errors[2], /第 6 行的列数与表头不一致/)
})

test('roster payload omits empty optional fields so omitted passwords stay untouched', () => {
  const payload = rosterPayload([
    { student_number: '20260001', display_name: '王一', cohort: '2026-A', password: 'Ngc0001' },
    { student_number: '20260002', display_name: '李二', cohort: null, password: null },
  ])

  assert.deepEqual(payload, {
    students: [
      { student_number: '20260001', display_name: '王一', cohort: '2026-A', password: 'Ngc0001' },
      { student_number: '20260002', display_name: '李二' },
    ],
  })
})

test('csrf token is read from either cookie spelling', () => {
  assert.equal(csrfTokenFromCookie('a=1; csrf_token=tok-1; b=2'), 'tok-1')
  assert.equal(csrfTokenFromCookie('__Host-csrf_token=tok-2'), 'tok-2')
  assert.equal(csrfTokenFromCookie('other=1'), null)
})

test('a signed-in non-administrator is offered a sign-out, not a sign-in loop', () => {
  const view = authFailureView(403, '演示学生一')

  assert.equal(view.action, 'logout-then-signin')
  assert.match(view.message, /演示学生一/)
  assert.match(view.message, /不是校园平台管理员/)
})

test('a signed-in non-administrator is still actionable without a resolvable name', () => {
  assert.equal(authFailureView(403, null).action, 'logout-then-signin')
})

test('an unauthenticated visitor is sent straight to sign-in', () => {
  const view = authFailureView(401)

  assert.equal(view.action, 'signin')
  assert.match(view.message, /登录/)
})

test('any other failure offers a retry rather than a misleading identity claim', () => {
  const view = authFailureView(503)

  assert.equal(view.action, 'reload')
  assert.doesNotMatch(view.message, /不是校园平台管理员/)
})
