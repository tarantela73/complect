import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'
import { test, expect } from '@playwright/test'

const BASE = 'http://localhost:3000'
const USERNAME = 'testadmin'
const PASSWORD = 'Admin1234!'

async function login(page: import('@playwright/test').Page) {
  await page.goto(`${BASE}/login`)
  await page.fill('#username', USERNAME)
  await page.fill('#password', PASSWORD)
  await page.click('button[type="submit"]')
  await page.waitForURL(`${BASE}/dashboard`, { timeout: 10_000 })
}

async function navigateToFiles(page: import('@playwright/test').Page) {
  // Use sidebar link to stay within the SPA — page.goto() resets Zustand in-memory auth store
  await page.getByRole('link', { name: 'Файлы' }).click()
  await expect(page.getByRole('heading', { name: 'Файлы' })).toBeVisible({ timeout: 8_000 })
}

test.describe('Files upload flow', () => {
  let tmpFile: string

  test.beforeAll(() => {
    tmpFile = path.join(os.tmpdir(), 'e2e-test-upload.txt')
    fs.writeFileSync(tmpFile, 'e2e test content ' + Date.now())
  })

  test.afterAll(() => {
    fs.rmSync(tmpFile, { force: true })
  })

  test('login → list → upload → detail → delete', async ({ page }) => {
    // ── 1. Login ──────────────────────────────────────────────────────────────
    await login(page)
    await expect(page).toHaveURL(`${BASE}/dashboard`)

    // ── 2. Navigate to Files ──────────────────────────────────────────────────
    await navigateToFiles(page)

    // ── 3. Upload ─────────────────────────────────────────────────────────────
    const fileInput = page.locator('input[type="file"]')
    await fileInput.setInputFiles(tmpFile)

    // Success banner appears with "Открыть" link
    const openLink = page.getByRole('link', { name: 'Открыть' })
    await expect(openLink).toBeVisible({ timeout: 15_000 })

    // New row with the filename appears in the table
    const fileName = path.basename(tmpFile)
    const fileRow = page.getByRole('link', { name: fileName })
    await expect(fileRow).toBeVisible()

    // ── 4. Open detail page ───────────────────────────────────────────────────
    await openLink.click()
    await expect(page).toHaveURL(/\/dashboard\/files\/[0-9a-f-]{36}/)

    // Filename and status badge are rendered
    await expect(page.getByRole('heading', { name: fileName })).toBeVisible()
    // Status is one of the known transient/available values
    const badge = page.locator('span').filter({ hasText: /Загружен|Загружается|Сканирование|Доступен/ }).first()
    await expect(badge).toBeVisible()

    // File metadata rows are present
    await expect(page.getByText('Имя файла')).toBeVisible()
    await expect(page.getByText('Тип содержимого')).toBeVisible()
    await expect(page.getByText('Размер')).toBeVisible()

    // ── 5. Delete ─────────────────────────────────────────────────────────────
    const deleteButton = page.getByRole('button', { name: 'Удалить' })
    await expect(deleteButton).toBeVisible()

    page.once('dialog', (dialog) => dialog.accept())
    await deleteButton.click()

    // Redirected back to list after successful delete
    await expect(page).toHaveURL(`${BASE}/dashboard/files`, { timeout: 10_000 })
  })

  test('upload → list row delete button → file removed from list', async ({ page }) => {
    await login(page)
    await navigateToFiles(page)

    // Upload second file with distinct name
    const tmpFile2 = path.join(os.tmpdir(), `e2e-row-delete-${Date.now()}.txt`)
    fs.writeFileSync(tmpFile2, 'row delete test')

    const fileInput = page.locator('input[type="file"]')
    await fileInput.setInputFiles(tmpFile2)
    const fileName2 = path.basename(tmpFile2)

    await expect(page.getByRole('link', { name: 'Открыть' })).toBeVisible({ timeout: 15_000 })
    await expect(page.getByRole('link', { name: fileName2 })).toBeVisible()

    // Delete via the row's ✕ button (stays on list page — SPA navigation, auth preserved)
    const row = page.getByRole('row').filter({ hasText: fileName2 })
    page.once('dialog', (dialog) => dialog.accept())
    await row.getByRole('button').last().click()

    // File row disappears from the list
    await expect(page.getByRole('link', { name: fileName2 })).not.toBeVisible({ timeout: 10_000 })

    fs.rmSync(tmpFile2, { force: true })
  })
})
