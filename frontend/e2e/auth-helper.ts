import type { Page, Response } from "@playwright/test";

const retryAfterSeconds = async (response: Response) => {
  const value = await response.headerValue("retry-after");
  if (!value || !/^(?:0|[1-9]\d?)$/.test(value)) return null;
  const seconds = Number(value);
  return seconds <= 60 ? seconds : null;
};

/**
 * Exercise server-side authentication throttling faithfully in the disposable
 * browser suite. Only one valid Retry-After retry is allowed.
 */
export async function clickAuthenticatedAction(
  page: Page,
  path: string,
  action: () => Promise<void>,
) {
  const waitForResponse = () => page.waitForResponse((response) =>
    new URL(response.url()).pathname === path
      && response.request().method() === "POST",
  );
  let responsePromise = waitForResponse();
  await action();
  let response = await responsePromise;
  const delay = response.status() === 429 ? await retryAfterSeconds(response) : null;
  if (delay === null) return response;

  await page.waitForTimeout(delay * 1_000);
  responsePromise = waitForResponse();
  await action();
  response = await responsePromise;
  return response;
}
