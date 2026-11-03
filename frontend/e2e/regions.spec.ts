import { expect, test, type Page } from '@playwright/test';

import { account, signIn, watchConsole } from './helpers';

async function go(page: Page, label: string, narrow: boolean) {
  if (narrow) await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: label }).first().click();
}

async function pickFirstPatient(page: Page) {
  await page.getByRole('combobox', { name: 'Patient' }).click();
  await page.getByRole('option').first().click();
}

test('doctor can open every clinical and operations region', async ({ page }, info) => {
  const narrow = info.project.name === 'narrow';
  const errors = watchConsole(page);
  await signIn(page, account('Doctor'));
  const shot = (name: string) => page.screenshot({ path: `test-results/${info.project.name}-${name}.png`, fullPage: true });

  await go(page, 'Intake', narrow);
  await expect(page.getByRole('heading', { name: 'Patient intake' })).toBeVisible();
  await shot('intake');

  await go(page, 'Handoffs', narrow);
  await expect(page.getByRole('heading', { name: 'Clinical handoff briefs' })).toBeVisible();
  const first = page.locator('main li button').first();
  if (await first.count()) {
    await first.click();
    await expect(page.getByText(/\d+\. Doctor review status/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('S - Situation')).toBeVisible();
    await expect(page.getByText('Named clinical handoff (not anonymized)')).toBeVisible();
    await shot('handoff');
  }

  await go(page, 'Medical records', narrow);
  await pickFirstPatient(page);
  await expect(page.getByRole('heading', { name: 'Document library' })).toBeVisible();
  const doc = page.getByRole('row').nth(1);
  if (await doc.count()) {
    await doc.click();
    await expect(page.getByText('Report text')).toBeVisible();
    await shot('document');
  }

  await go(page, 'Medication & safety', narrow);
  await pickFirstPatient(page);
  await expect(page.getByText('Evidence-backed safety screen')).toBeVisible();
  await expect(page.getByText('does not mean the combination is safe').first()).toBeVisible({ timeout: 20_000 });
  await shot('medication');

  await go(page, 'Agent mission control', narrow);
  await expect(page.getByText('LangGraph workflow')).toBeVisible();
  await expect(page.getByRole('img', { name: 'Agent workflow graph' })).toBeVisible();
  await shot('mission-control');

  await go(page, 'Workflow & approvals', narrow);
  await expect(page.getByText('Recent decisions and record writes')).toBeVisible();
  await shot('approvals');

  await go(page, 'Observability', narrow);
  await expect(page.getByText('Agent latency')).toBeVisible();
  await shot('observability');

  // Doctors have no governance or administration access.
  await expect(page.getByRole('link', { name: 'Audit & governance' })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('administrator: audit chain, users and configuration, no clinical regions', async ({ page }, info) => {
  const narrow = info.project.name === 'narrow';
  const errors = watchConsole(page);
  await signIn(page, account('Administrator'));
  await go(page, 'Audit & governance', narrow);
  await expect(page.getByText(/Audit chain intact/)).toBeVisible({ timeout: 20_000 });
  await page.screenshot({ path: `test-results/${info.project.name}-audit.png`, fullPage: true });
  await go(page, 'Administration', narrow);
  await expect(page.getByRole('table', { name: 'Users' })).toBeVisible();
  await page.getByRole('tab', { name: 'Configuration' }).click();
  await expect(page.getByText('Trace privacy')).toBeVisible();
  await page.screenshot({ path: `test-results/${info.project.name}-admin.png`, fullPage: true });
  await page.goto('/handoffs');
  await expect(page.getByRole('link', { name: 'Handoffs' })).toHaveCount(0);
  expect(errors.filter((e) => !e.includes('403'))).toEqual([]);
});
