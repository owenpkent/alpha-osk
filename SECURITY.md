# Security Policy

## Reporting a Vulnerability

If you believe you have found a security vulnerability in Alpha-OSK,
please report it privately so it can be fixed before public disclosure.

**Preferred:** use GitHub's
[private vulnerability reporting](https://github.com/owenpkent/alpha-osk-releases/security/advisories/new)
on the releases repository.

**Email fallback:** owenpkent@gmail.com with the subject line
`SECURITY: alpha-osk`.

Please include:

- A description of the vulnerability and its impact.
- Steps to reproduce, or a minimal proof-of-concept.
- The Alpha-OSK version (visible in Settings or `src/__version__.py`).
- The OS and Python version you observed it on.

You should expect an acknowledgement within 7 days. If a fix is
warranted, a patched release will be published to
`owenpkent/alpha-osk-releases` and the auto-updater will pick it up on
the next launch.

## Scope

In scope:

- The Alpha-OSK Python source under `src/`.
- The QML UI under `qml/`.
- Build and signing pipelines under `build/`.
- The opt-in telemetry worker under `backend/cf-worker/`.

Out of scope:

- Issues that require physical access to an unlocked machine where the
  attacker could already type into the target app directly.
- Vulnerabilities in third-party dependencies (please report upstream;
  we will pick up the fix on the next release).
- Denial of service that requires the attacker to already control the
  user's keyboard input.

## Supported Versions

Only the latest released version receives security fixes. Older
versions can be upgraded via the in-app auto-updater
(*Settings -> Updates*) or by downloading the current installer from
the releases repository.
