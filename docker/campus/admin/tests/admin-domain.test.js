import assert from 'node:assert/strict'
import test from 'node:test'

import { allowanceAmount, authFailureView, campusDay, csrfTokenFromCookie, defaultAllowancePayload, knowledgeLimitPayload, parseRosterCsv, rosterPayload, slotCapacityPayload, slotEnded } from '../assets/admin-domain.js'

test('campus day follows UTC+8 regardless of the local clock', () => {
  assert.equal(campusDay(new Date('2026-08-11T16:30:00Z')), '2026-08-12')
})

test('slot end comparison uses the server instant', () => {
  const slot = { ends_at: '2026-08-12T04:00:00Z' }

  assert.equal(slotEnded(slot, new Date('2026-08-12T03:59:59Z')), false)
  assert.equal(slotEnded(slot, new Date('2026-08-12T04:00:00Z')), true)
})

test('roster CSV requires 学号/姓名, allows 班级, and rejects anything else', () => {
  assert.deepEqual(parseRosterCsv('').errors, ['文件为空'])
  assert.match(parseRosterCsv('name,pwd\n1,2').errors[0], /表头/)
  assert.match(parseRosterCsv('student_number,display_name,grade\n1,王,3').errors[0], /无法识别/)
  assert.match(parseRosterCsv('学号,姓名,密码\n20260001,王一,init').errors[0], /无法识别/)
  // 班级 is part of the template, so it must parse rather than be rejected.
  const { rows, errors } = parseRosterCsv('学号,姓名,班级\n20260001,王一,一班')
  assert.deepEqual(errors, [])
  assert.deepEqual(rows, [{ student_number: '20260001', display_name: '王一', cohort: '一班' }])
})

test('roster CSV parses the two identifying columns and reports bad rows with line numbers', () => {
  const { rows, errors } = parseRosterCsv([
    '学号,姓名',
    '20260001,王一',
    '20260002,李二',
    '20260002,李二重复',
    ',缺学号',
    '20260003,赵三,多余',
  ].join('\n'))

  assert.deepEqual(rows, [
    { student_number: '20260001', display_name: '王一', cohort: null },
    { student_number: '20260002', display_name: '李二', cohort: null },
  ])
  assert.equal(errors.length, 3)
  assert.match(errors[0], /第 4 行学号 20260002 重复/)
  assert.match(errors[1], /第 5 行缺少学号或姓名/)
  assert.match(errors[2], /第 6 行的列数与表头不一致/)
})

test('roster payload always states the cohort, blank when the file had none', () => {
  // Sending null rather than omitting keeps one shape; the backend is what
  // refuses to let a blank wipe an existing class.
  assert.deepEqual(
    rosterPayload([
      { student_number: '20260001', display_name: '王一' },
      { student_number: '20260002', display_name: '李二', cohort: '一班' },
    ]),
    {
      students: [
        { student_number: '20260001', display_name: '王一', cohort: null },
        { student_number: '20260002', display_name: '李二', cohort: '一班' },
      ],
    },
  )
})

test('knowledge limits accept whole numbers above zero for both fields', () => {
  assert.deepEqual(knowledgeLimitPayload('1', '3'), {
    max_datasets_per_workspace: 1,
    max_documents_per_dataset: 3,
  })
})

test('knowledge limits reject zero, fractions, text and out-of-range values', () => {
  for (const [datasets, documents] of [['0', '3'], ['1', '0'], ['1.5', '3'], ['x', '3'], ['1', ''],
    ['2147483648', '3'], ['1', '2147483648']]) {
    const payload = knowledgeLimitPayload(datasets, documents)
    assert.ok('error' in payload, `${datasets}/${documents} should be rejected`)
  }
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

test('unified slot capacity accepts whole numbers above zero', () => {
  assert.deepEqual(slotCapacityPayload('120'), { capacity: 120 })
  assert.deepEqual(slotCapacityPayload(' 7 '), { capacity: 7 })
  assert.deepEqual(slotCapacityPayload('007'), { capacity: 7 })
})

test('unified slot capacity rejects zero, negatives, fractions and text', () => {
  for (const raw of ['0', '-1', '1.5', '', 'abc', '1e3', '+5']) {
    const result = slotCapacityPayload(raw)
    assert.equal(result.capacity, undefined, `expected ${JSON.stringify(raw)} to be rejected`)
    assert.match(result.error, /不小于 1 的整数/)
  }
})

test('unified slot capacity rejects values the database column cannot hold', () => {
  assert.match(slotCapacityPayload('2147483648').error, /超出允许范围/)
  assert.deepEqual(slotCapacityPayload('2147483647'), { capacity: 2147483647 })
})

test('the default allowance takes an amount, including zero', () => {
  assert.deepEqual(defaultAllowancePayload('10'), { default_allowance_usd: 10 })
  assert.deepEqual(defaultAllowancePayload(' 12.5 '), { default_allowance_usd: 12.5 })
  // Zero is a real setting: a platform that grants no model quota at all.
  assert.deepEqual(defaultAllowancePayload('0'), { default_allowance_usd: 0 })
})

test('the default allowance rejects negatives, text and over-precise amounts', () => {
  for (const raw of ['-1', '', 'abc', '1e3', '+5', '10.123', '1,5']) {
    const result = defaultAllowancePayload(raw)
    assert.equal(result.default_allowance_usd, undefined, `expected ${JSON.stringify(raw)} to be rejected`)
    assert.match(result.error, /最多两位小数/)
  }
})

test('the default allowance rejects values the database column cannot hold', () => {
  assert.match(defaultAllowancePayload('10000000000').error, /超出允许范围/)
  assert.deepEqual(defaultAllowancePayload('9999999999.99'), { default_allowance_usd: 9999999999.99 })
})

test('amounts read back from the API lose the column padding but keep the value', () => {
  assert.equal(allowanceAmount('10.0000'), '10')
  assert.equal(allowanceAmount('12.5000'), '12.5')
  assert.equal(allowanceAmount(null), '')
})
