# M1 dashboard use, upgrade, and acceptance

> **English — single source of truth** · [中文](m1-dashboard_zh.md)

This guide describes the stable M1 workflows after installing MateScope alongside TeslaMate. It does not authorize a production deployment, database grant, image publication, or rollback.

## Use the dashboard

Sign in at the configured HTTPS origin. Select the vehicle once, then select a calendar range using the date picker. **All history** resolves the earliest available record through the selected end date; it is not limited to 90 days. Use **Overview** for concise latest recorded values, period summaries, and recent records. Latest recorded values are independent of the selected period.

Use **Trips** and **Charges** to open a paged list, a detail, and its charts. Returning restores the vehicle, period, page, scroll position, and focus. Missing data, unfinished records, and unavailable optional PostgreSQL capabilities are displayed as such; they do not mean zero or live data. If a map or chart fails, its summary and the other sections remain usable.

## Prepare and perform an upgrade

Before changing an image, make a MateScope-owned backup and record the current image reference. Follow the existing Compose invocation exactly and target only the `matescope` service. The [Compose guide](existing-teslamate-compose.md) explains how to preserve the existing TeslaMate project and volumes.

Start the new image with the existing administrator account. Base history remains usable with the legacy dedicated role. Review the current [read-only account guide](postgresql-readonly.md), then, in a separately authorized maintenance window, run its idempotent minimal-grant upgrade for the explicitly named dedicated role. Do not run it as the application and do not grant access to unrelated tables or columns. Retest the saved PostgreSQL connection and verify the newly available detail, series, and latest-value capabilities.

Check a selected vehicle, a short date range, All history, summaries, and representative trip and charge details. If the prior image cannot read the new configuration after a rollback, restore the pre-upgrade MateScope backup. Restoring invalidates sessions and recovery codes, preserves an enabled authenticator, keeps `reset-password` from disabling 2FA, and requires the explicit `reset-2fa` operator action to disable 2FA.

## Owner acceptance walkthrough

Use a separately authorized synthetic or production target and keep real vehicle data out of reports. Confirm the following expected outcomes:

1. Overview has concise latest values, four summary positions, and recent records without a chart wall.
2. Changing dates updates period summaries and records but does not redefine latest recorded values.
3. All history reaches the earliest record; DST, leap-day, and cross-year ranges use the intended local-calendar boundaries.
4. Summary to list to detail and Back restores state. Switching vehicle while a request is pending cannot replace the new selection with an old response.
5. Missing, unfinished, map, and chart data retain explicit partial/error states. Currency remains unqualified when unset.
6. On phone and desktop, navigation, date selection, record access, theme, language, keyboard focus, touch targets, and bottom safe area are usable without horizontal page scrolling.

Browser automation is evidence for Chromium and WebKit only. Native-device behavior, production schema compatibility, image publication, and deployment are separate acceptance states.
