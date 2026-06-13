# Security Policy

## Supported versions

GogMail follows a roll-forward model: fixes land on the latest release line.
Please upgrade to the most recent version before reporting a vulnerability.

| Version | Supported          |
| ------- | ------------------ |
| 1.3.x   | :white_check_mark: |
| < 1.3   | :x:                |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Report vulnerabilities privately through one of:

- **GitHub Security Advisories** — preferred. Use the
  [*Report a vulnerability*](https://github.com/olafkfreund/gogmail/security/advisories/new)
  button on the repository's **Security** tab.
- **Email** — <olaf@freundcloud.com>, with `[gogmail security]` in the subject.

Please include enough detail to reproduce: affected version, environment, steps,
and the impact you observed. We aim to acknowledge reports within a few days and
will keep you updated as we investigate and prepare a fix. Coordinated disclosure
is appreciated — we will agree on a public-disclosure timeline with you.

## Security posture

### No secrets are bundled — ever

GogMail is a **thin TUI over external services** and is built so that **no
credentials or personal data are ever placed in a released artifact**:

- The app never holds Google credentials — the `gog` CLI owns authentication.
- The Gemini API key (`GEMINI_API_KEY` / `geminiApiKeyFile`) and Zoom credentials
  (`GOG_ZOOM_*`) are read **at runtime from your environment**, never compiled in.
- The Nix modules read secret files at runtime via a wrapper so keys do not land
  world-readable in the Nix store.

The full breakdown of what is and isn't in a package — and why — is documented in
[`PACKAGING.md`](PACKAGING.md).

### Signed releases

Release artifacts are protected by a `SHA256SUMS` manifest that is **cosign-signed
keylessly** (Sigstore/OIDC) by the GitHub Actions release workflow — there is no
long-lived signing key that could leak. Before installing, verify both the
checksums and the signature as described in
[`PACKAGING.md`](PACKAGING.md#verifying-before-you-trust). **If verification fails, do not
install.**

## Scope

In scope: the GogMail application code, its packaging, and its release pipeline.

Out of scope: vulnerabilities in upstream dependencies (report those upstream —
e.g. the `gog`/gogcli tool, Textual, or the Gemini/Zoom APIs), and issues that
require an already-compromised local machine.
