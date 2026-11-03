import { defineConfig, devices } from '@playwright/test';

// Runs against the locally started stack (scripts/start.ps1): web on 5173, API on 8000, seeded synthetic data.
export default defineConfig({
  testDir: './e2e',
  outputDir: 'test-results/pw', // screenshots live in test-results/ and survive runs
  timeout: 60_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.MB_WEB_URL ?? 'http://127.0.0.1:5173',
    channel: process.env.PW_CHANNEL ?? 'msedge',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'narrow', use: { ...devices['Pixel 7'], channel: process.env.PW_CHANNEL ?? 'msedge' } },
  ],
});
