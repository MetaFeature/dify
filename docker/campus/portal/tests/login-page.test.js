import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const loginPage = await readFile(new URL('../index.html', import.meta.url), 'utf8')

test('student login asks for an account and password without embedding credentials', () => {
  assert.match(loginPage, /<label>账号<input[^>]+name="studentNumber"[^>]+autocomplete="username"/)
  assert.match(loginPage, /<label>密码<input[^>]+name="loginCode"[^>]+type="password"[^>]+autocomplete="current-password"/)
  assert.match(loginPage, /placeholder="请输入学生账号"/)
  assert.match(loginPage, /placeholder="请输入密码"/)
  assert.doesNotMatch(loginPage, /value="[^"]+"/)
})
