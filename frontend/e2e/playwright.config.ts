import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './specs',
  timeout: 30_000,
  retries: 0,
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:3000',
    launchOptions: {
      executablePath: '/home/sever/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome',
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
    },
    headless: true,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
