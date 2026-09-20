import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './specs',
  outputDir: '/artifacts/e2e',
  timeout: 30_000,
  workers: 1,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8080',
    headless: true,
    trace: 'off',
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
