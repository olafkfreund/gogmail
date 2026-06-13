# GogMail packaging, provenance & trust

This document explains **what** the distributed artifacts are, **how** they are
built, and **how to verify** them. It is written so a security-conscious user
can trust an install without reading the build scripts.

## What GogMail is (and what it is not)

GogMail is a terminal (TUI) client for Google Workspace. It is a **thin UI over
external services** — it does not talk to Google directly:

- **Google Workspace** access goes through the separately-installed **`gog`
  CLI** (gogcli), which owns all Google authentication. GogMail never sees or
  stores Google credentials.
- **AI, voice transcription and text-to-speech** go through the **Gemini API**,
  keyed by the `GEMINI_API_KEY` you provide at runtime.
- **Zoom meeting creation** goes through the Zoom REST API using
  `GOG_ZOOM_*` credentials you provide at runtime.

### No secrets are bundled — ever

The packages contain **only application code and its open-source Python
dependencies**. There are **no API keys, tokens, Google/Zoom credentials, or
personal data** in any artifact. Every secret is read at runtime from an
environment variable or a file path you control:

| Secret | Source at runtime | In the package? |
|---|---|---|
| Google auth | managed entirely by `gog` (its own keyring) | no |
| `GEMINI_API_KEY` | your environment | no |
| `GOG_ZOOM_ACCOUNT_ID` / `GOG_ZOOM_CLIENT_ID` / `GOG_ZOOM_CLIENT_SECRET` | your environment | no |

You can confirm this yourself: the SBOM lists every component, and
`dpkg-deb -c` / `rpm -qlp` list every file shipped.

## The artifacts

A release publishes:

| Artifact | What it is |
|---|---|
| `gogmail.pyz` | A self-contained [`shiv`](https://shiv.readthedocs.io/) zipapp bundling GogMail **and** its Python deps (textual, requests, rich, …). Runs on any system `python3 >= 3.10`. |
| `gogmail_<ver>_all.deb` | Debian/Ubuntu package wrapping the zipapp at `/usr/lib/gogmail/gogmail.pyz` with a `/usr/bin/gogmail` launcher. |
| `gogmail-<ver>.noarch.rpm` | Fedora/RHEL/openSUSE package, same layout. |
| `gogmail.cdx.json` | **SBOM** in CycloneDX format (every component + version + license). |
| `gogmail.spdx.json` | **SBOM** in SPDX format (same data, alternate standard). |
| `grype-report.txt` / `pip-audit.json` | **Vulnerability / dependency scan** results for the bundled dependencies. |
| `SHA256SUMS` | SHA-256 checksums of every artifact above. |
| `SHA256SUMS.cosign.bundle` (+ `.pem`/`.sig`) | A **keyless [cosign](https://docs.sigstore.dev/) signature** of `SHA256SUMS`, tying it to the GitHub Actions release workflow via OIDC — no long-lived signing key exists to leak. |

The package is **arch-independent** (`all`/`noarch`): the bundled wheels are
pure-Python.

## How it is built

Everything is built **from source in CI** (`.github/workflows/release.yml`),
triggered by a version tag. The same steps run locally via
`packaging/build.sh`:

1. **Bundle** — `shiv -c gogmail -o dist/gogmail.pyz .` builds a wheel from this
   repo and vendors it plus its dependencies into one executable zipapp.
2. **Package** — `nfpm` emits the `.deb` and `.rpm` from `packaging/nfpm.yaml`
   (one config, both formats).
3. **SBOM** — `syft` scans the project and emits CycloneDX + SPDX.
4. **Scan** — `grype` checks the SBOM for known CVEs and `pip-audit` checks the
   Python dependency set; both reports are published with the release.
5. **Checksum** — `sha256sum` over all artifacts → `SHA256SUMS`.
6. **Sign** — `cosign sign-blob` signs `SHA256SUMS` **keylessly** using the
   workflow's OIDC identity (Sigstore). No signing secret is stored in the repo
   or CI.

Runtime requirements declared by the packages: `python3 (>= 3.10)`. Optional
*recommends*: `ffmpeg`, `espeak-ng`, `alsa-utils`, `wl-clipboard`, `xclip` (for
voice and clipboard). **`gog` (gogcli)** is a hard prerequisite but is not in
distro repositories, so install it separately from
<https://github.com/steipete/gogcli> and run `gog auth login`.

## Installing

```bash
# Debian / Ubuntu
sudo apt install ./gogmail_<ver>_all.deb

# Fedora / RHEL / openSUSE
sudo dnf install ./gogmail-<ver>.noarch.rpm

# Or run the zipapp directly (no install)
python3 gogmail.pyz
```

NixOS users should use the flake instead (`nix run github:olafkfreund/gogmail`)
— see `nixos_install.md`.

## Verifying before you trust

```bash
# 1. Checksums match
sha256sum -c SHA256SUMS

# 2. The signature on SHA256SUMS is genuine and came from this repo's release
#    workflow (keyless / Sigstore):
cosign verify-blob \
  --bundle SHA256SUMS.cosign.bundle \
  --certificate-identity-regexp 'https://github.com/olafkfreund/gogmail/.+' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  SHA256SUMS

# 3. Inspect exactly what a package installs (no surprises, no secrets):
dpkg-deb -c gogmail_<ver>_all.deb        # Debian
rpm -qlp gogmail-<ver>.noarch.rpm        # RPM

# 4. Review the bill of materials and the scan results:
cat gogmail.cdx.json | jq '.components[].name'
cat grype-report.txt
```

If the checksum check or the cosign verification fails, **do not install** —
the artifact has been tampered with or did not come from the official release.
