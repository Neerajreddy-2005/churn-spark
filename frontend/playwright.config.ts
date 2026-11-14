import { defineConfig } from '@playwright/test'

// Credentials live in the git-ignored .env.e2e (E2E_EMAIL, E2E_PASSWORD).
try { process.loadEnvFile('.env.e2e') } catch { /* fall back to real env vars */ }

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'e2e-results',
  workers: 1, // one signed-in user, one live backend: keep it gentle
  retries: 0,
  reporter: [['list'], ['json', { outputFile: '../backend/pipeline/data/evidence/e2e_live.json' }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'https://churn-spark.netlify.app',
    channel: 'chrome', // the installed Chrome; no browser download
    screenshot: 'on',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true } },
  ],
})
