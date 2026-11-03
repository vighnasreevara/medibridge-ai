import { expect, test } from '@playwright/test';

import { account, scenario, signIn, watchConsole } from './helpers';

test('wrong password shows a generic error', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Email').fill(account('Doctor').email);
  await page.getByLabel('Password').fill('definitely-not-the-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert')).toHaveText('Email or password is incorrect.');
});

test('doctor: overview, registry, patient record, live badge', async ({ page }, info) => {
  const errors = watchConsole(page);
  await signIn(page, account('Doctor'));
  await expect(page.getByText('Synthetic data only')).toBeVisible();
  await expect(page.getByText('Encounters today')).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: 'Live' })).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `test-results/${info.project.name}-overview.png`, fullPage: true });

  const allergic = scenario('ace_inhibitor_allergy');
  await page.goto('/patients');
  await page.getByLabel('Search patients').fill(allergic.mrn!);
  const row = page.getByText(allergic.mrn!).filter({ visible: true }).first();
  await expect(row).toBeVisible();
  await page.screenshot({ path: `test-results/${info.project.name}-registry.png`, fullPage: true });
  await row.click();
  await expect(page.getByRole('tab', { name: 'Medications' })).toBeVisible();
  await page.getByRole('tab', { name: 'Allergies' }).click();
  await expect(page.getByText('Lisinopril', { exact: false }).first()).toBeVisible();
  await page.getByRole('tab', { name: 'Results' }).click();
  await expect(page.getByText('Laboratory and imaging measurements')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `test-results/${info.project.name}-patient.png`, fullPage: true });
  expect(errors).toEqual([]);
});

test('admin sees no clinical navigation', async ({ page }, info) => {
  await signIn(page, account('Administrator'));
  if (info.project.name === 'narrow') await page.getByRole('button', { name: 'Open navigation' }).click();
  await expect(page.getByRole('link', { name: 'Patients' }).first()).toBeAttached();
  await expect(page.getByRole('link', { name: 'Encounters' })).toHaveCount(0);
});

test('nurse: patient registry finds a returning patient by MRN and date of birth', async ({ page }, info) => {
  const errors = watchConsole(page);
  const who = scenario('statin_macrolide');
  await signIn(page, account('Nurse'));
  await page.goto('/intake');
  await expect(page.getByText('Patient Registry (EMPI)')).toBeVisible();
  await page.getByLabel('MRN (wristband)').fill(who.mrn!);
  await page.getByLabel('Date of birth').first().fill(who.birth_date!);
  await page.getByRole('button', { name: 'Find patient' }).click();
  await expect(page.getByRole('status').getByText(who.mrn!)).toBeVisible();
  await expect(page.getByRole('button', { name: "Start today's intake" })).toBeVisible();
  // A wrong date of birth for the same MRN is refused, not "closest match".
  await page.getByLabel('Date of birth').first().fill('1901-01-01');
  await page.getByRole('button', { name: 'Find patient' }).click();
  await expect(page.getByText('registered with a different date of birth')).toBeVisible();
  await page.screenshot({ path: `test-results/${info.project.name}-registry-intake.png`, fullPage: true });
  expect(errors).toEqual([]);
});
