# Not hiding the automated browser

Whether the browser collector should pretend to be a person browsing, so that ChatGPT, Gemini and Claude cannot tell that a script is driving it.

## Decision

On 2026-10-13 we decided not to hide the automation.
The collector keeps using Playwright's Chrome for Testing as it is, with the automation signals it normally sends.
When a site shows a CAPTCHA, a person solves it or the observation is recorded as `status: captcha`.

The question came up because logged-out Claude and ChatGPT showed CAPTCHAs on most questions in the checks of 2026-10-12.
Disguising the browser as a person's would probably have reduced them, although claude.ai cannot be used logged out even past the CAPTCHA (see [web-app-access.md](web-app-access.md)).
We did not do it, for these reasons:

- A CAPTCHA is how a site says it does not want automated visitors. Hiding the automation, for example by removing `navigator.webdriver`, suppressing the "controlled by automated test software" banner or using a stealth plugin, gets around that control. The terms of use of OpenAI, Google and Anthropic forbid circumventing their protections.
- The project already states that it does not bypass CAPTCHAs (README, `docs/testing.md`). Hiding the automation would bypass them by other means.
- A reviewer would ask how far answers obtained by evading a site's controls can be trusted, and how the study's methods section could describe it.

## Why the browser is visible as automated

Chrome for Testing is a Chrome build that Google publishes for automated testing.
Playwright installs a fixed version of it (build 1243, Chrome 153, with Playwright 1.63).
Every question gets a fresh browser with no cookies or login, the same locale (`en-US`) and timezone (`UTC`), and no permission to read the location.
This keeps the source IP as the only thing that differs between the US and Israel conditions.

The same build sets `navigator.webdriver` and shows an automation banner.
Sites can see both, and they are probably one reason for the frequent CAPTCHAs.
Using a person's everyday Chrome instead would see fewer CAPTCHAs but would break the controls above: an app could learn the location from the account, the browsing history or the system timezone.

## What we do instead

- A person solves CAPTCHAs. With `browser.human_captcha_enabled: true` and a visible browser, the run pauses, prints `HUMAN ACTION NEEDED` and waits up to `human_captcha_timeout_seconds`. A solved CAPTCHA is recorded as `manual_intervention: true`, so it can be reported. With `browser.stay_in_background`, new windows hand focus back; a CAPTCHA wait still brings the window forward once and can ping you via `experiment.alerts`. Counts from the saved checks are in [captchas.md](captchas.md).
- The API collectors do not need any of this. They are the study's main source; the web apps add whether a consumer app uses the IP location.

## Options not yet taken

These would reduce CAPTCHAs without hiding the automation:

- One saved, logged-out browser profile per location and app, so that a CAPTCHA solved once is remembered. US and Israel profiles would never share cookies. Later questions would then carry cookies from earlier ones, which the methods section would have to state.
- Pauses of a minute or two between questions. Twelve fresh browsers within a few minutes from one IP is the pattern that triggers CAPTCHAs.
- Asking OpenAI, Google and Anthropic for research access, or for confirmation of whether their consumer apps use the IP location.

## Related

- [captchas.md](captchas.md), [docs/scheduling.md](../docs/scheduling.md), [docs/README.md](../docs/README.md)
