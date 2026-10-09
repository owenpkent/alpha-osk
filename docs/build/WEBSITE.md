# The website (alphaosk.com)

This doc holds the full write-up behind the CLAUDE.md section of the same name.

## The website (alphaosk.com)

Source in `owenpkent/alpha-osk-website`, cloned alongside this repo as `alpha-osk-website`. One
static page, no build step, no dependencies, deployed from the repo root on Netlify. It is the
same shape as the author's other site repos (`reflex-website`, `okstudio-website`): plain HTML
plus one stylesheet and one script, with `netlify.toml` carrying the security headers, cache
rules and the apex redirect.

**A release never edits it, and that is the point.** `scripts/alphaosk.js` reads the latest tag
from `api.github.com/repos/owenpkent/alpha-osk-releases/releases/latest` on page load and writes
it into the download buttons, so there is no version string in that repo to go stale. If the
request fails (rate limit, offline, a blocked origin) the buttons keep the static text the markup
already carried, which is why none of them say a version number on their own. Two consequences:
the CSP in `netlify.toml` must keep `https://api.github.com` in `connect-src`, and a release that
does not appear on the site is a releases-API question, never a site deploy question.

**Its content is derived from this repo, so this repo is the authority.** The copy comes from
`README.md` and `docs/PRIVACY.md`. When the two disagree the site is stale, and the claims most
likely to go stale are named in that repo's own README: the platform table (macOS is listed as
not yet released), the note that the telemetry endpoint is not deployed, and the test count.
Changing any of those three here means changing them there in the same sitting.

**The screenshots and icons are generated, not hand-made.** `images/screenshots/` is copied from
this repo's `assets/screenshots/`, which `scripts/capture_screenshots.py` produces against a
sandboxed config directory and a demo vocabulary, so nothing on the public site is anybody's real
typing. The favicons and the Open Graph card come from this repo's logo through that repo's
`tools/gen_assets.py` (PySide6, no new dependency), so the site's icon and the application's icon
cannot drift apart.

