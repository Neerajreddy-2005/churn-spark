import { test, expect, type Page } from '@playwright/test'

// Read-only walk of the live app: sign in, open each main page, require a clean console.
const PAGES = ['/', '/upload', '/runs', '/datasets', '/customers']

const email = process.env.E2E_EMAIL
const password = process.env.E2E_PASSWORD

function watch(page: Page) {
  const problems: string[] = []
  // Known and harmless: the CSP blocks the inline script Netlify injects (its toolbar), by design.
  const NETLIFY_INLINE = /violates the following Content Security Policy directive 'script-src 'self''/
  page.on('console', (m) => { if (m.type() === 'error' && !NETLIFY_INLINE.test(m.text())) problems.push(`console: ${m.text()}`) })
  page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`))
  // ERR_ABORTED = the app cancelled an in-flight request while navigating; not a failure.
  page.on('requestfailed', (r) => { if (r.failure()?.errorText !== 'net::ERR_ABORTED') problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`) })
  page.on('response', (r) => { if (r.status() >= 500) problems.push(`http ${r.status()}: ${r.url()}`) })
  return problems
}

test.beforeEach(() => { if (!email || !password) throw new Error('Set E2E_EMAIL and E2E_PASSWORD in frontend/.env.e2e') })

test('signed-out visitors are sent to the login page', async ({ page }) => {
  const problems = watch(page)
  await page.goto('/customers')
  await expect(page).toHaveURL(/\/login/)
  await expect(page.getByRole('heading', { name: /sign in to churn-spark/i })).toBeVisible()
  expect(problems).toEqual([])
})

test('wrong password shows one generic error', async ({ page }) => {
  await page.goto('/login')
  await page.locator('input[type=email]').fill(email!)
  await page.locator('input[type=password]').fill('definitely-not-the-password-1!')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert').or(page.getByText(/incorrect|invalid|could not sign/i)).first()).toBeVisible()
  await expect(page).toHaveURL(/\/login/)
})

test('signed-in user can open every main page with a clean console', async ({ page }) => {
  const problems = watch(page)
  await page.goto('/login')
  await page.locator('input[type=email]').fill(email!)
  await page.locator('input[type=password]').fill(password!)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page).not.toHaveURL(/\/login/, { timeout: 30_000 })

  for (const path of PAGES) {
    await page.goto(path)
    await expect(page, `${path} should not bounce to login`).not.toHaveURL(/\/login/)
    await expect(page.locator('main, [role=main]').first(), `${path} main area`).toBeVisible()
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: `e2e-results/shots/${test.info().project.name}${path === '/' ? '-overview' : path.replace('/', '-')}.png`, fullPage: true })
  }
  expect(problems, 'console/page/network problems').toEqual([])
})
