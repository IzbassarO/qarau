import type { Page } from '@playwright/test';

/** Follow the same collapsed-navigation controls as a phone/tablet user. */
export async function openNavigation(page: Page) {
  const navigation = page.getByRole('navigation', { includeHidden: true });
  // Sign-in is asynchronous. Wait for the shell before deciding whether its
  // navigation needs opening; the desktop trigger stays hidden by design.
  await navigation.waitFor({ state: 'attached' });
  if (!(await navigation.isVisible())) await page.locator('.sidebar-trigger').click();
}

export async function closeNavigation(page: Page) {
  if (await page.locator('.sidebar-backdrop').isVisible())
    await page.locator('.sidebar-trigger').click();
}

export async function openTools(page: Page) {
  const drawer = page.locator('.viewer-tools-drawer');
  if ((await drawer.getAttribute('open')) === null) await drawer.locator('summary').click();
}

export async function closeTools(page: Page) {
  const drawer = page.locator('.viewer-tools-drawer');
  if ((await drawer.getAttribute('open')) !== null) await drawer.locator('summary').click();
}

export async function openDetails(page: Page) {
  const drawer = page.locator('.viewer-panel-drawer');
  if ((await drawer.getAttribute('open')) === null) await drawer.locator('summary').click();
}

export async function closeDetails(page: Page) {
  const drawer = page.locator('.viewer-panel-drawer');
  if ((await drawer.getAttribute('open')) !== null) await drawer.locator('summary').click();
}
