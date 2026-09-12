import { test, expect } from '@playwright/test'

test('developer login page and browser requests use the gateway origin', async ({ page }) => {
  await page.goto('/login')
  await expect(page.locator('#username')).toBeVisible()
  await expect(page.locator('#password')).toBeVisible()
  const result = await page.evaluate(async () => {
    const response = await fetch('http://localhost:8000/public/ping/', { credentials: 'include' })
    return { status: response.status, body: await response.json() }
  })
  expect(result).toEqual({ status: 200, body: { status: 'ok' } })
})

test('gateway preflight permits the canonical frontend origin', async ({ request }) => {
  const response = await request.fetch('http://localhost:8000/api/v1/auth/login/', {
    method: 'OPTIONS',
    headers: {
      Origin: 'http://localhost:8080',
      'Access-Control-Request-Method': 'POST',
      'Access-Control-Request-Headers': 'content-type',
    },
  })
  expect(response.ok()).toBeTruthy()
  expect(response.headers()['access-control-allow-origin']).toBe('http://localhost:8080')
  expect(response.headers()['access-control-allow-credentials']).toBe('true')
})
