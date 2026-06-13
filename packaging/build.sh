#!/usr/bin/env bash
# Build GogMail's distributable artifacts: a self-contained zipapp, .deb + .rpm,
# an SBOM (CycloneDX + SPDX), dependency/vulnerability scans, and SHA256SUMS.
# Signing of SHA256SUMS happens in CI (keyless cosign) — see release.yml.
#
# Requires on PATH: shiv, nfpm, syft, grype, pip-audit, sha256sum, python3.
# (In CI these are installed; locally you can prefix each with `nix run nixpkgs#<tool> --`.)
#
# Usage: GOGMAIL_VERSION=1.2.3 packaging/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="${GOGMAIL_VERSION:-$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)}"
export GOGMAIL_VERSION="$VERSION"
mkdir -p dist
echo ">> Building GogMail $VERSION"

echo ">> [1/6] Self-contained zipapp (shiv)"
shiv -c gogmail -o dist/gogmail.pyz .

echo ">> [2/6] Linux packages (.deb + .rpm via nfpm)"
nfpm package -f packaging/nfpm.yaml -p deb -t dist/
nfpm package -f packaging/nfpm.yaml -p rpm -t dist/

echo ">> [3/6] SBOM (CycloneDX + SPDX via syft)"
# Scan the bundled site-packages so the SBOM reflects exactly what ships.
rm -rf dist/_pyz && python3 -c "import zipfile; zipfile.ZipFile('dist/gogmail.pyz').extractall('dist/_pyz')"
syft dir:dist/_pyz/site-packages -o cyclonedx-json=dist/gogmail.cdx.json -o spdx-json=dist/gogmail.spdx.json -q
python3 - <<'PY'
import json
c = json.load(open("dist/gogmail.cdx.json")).get("components", [])
reqs = sorted(f"{x['name']}=={x['version']}" for x in c
              if x.get("name") and x["name"] != "gogmail" and x.get("version"))
open("dist/requirements.lock.txt", "w").write("\n".join(reqs) + "\n")
PY

echo ">> [4/6] Vulnerability + dependency scans"
grype "sbom:dist/gogmail.cdx.json" -o table | tee dist/grype-report.txt
pip-audit -r dist/requirements.lock.txt --format json -o dist/pip-audit.json || true

echo ">> [5/6] Checksums"
rm -rf dist/_pyz
( cd dist && sha256sum gogmail.pyz gogmail_*_all.deb gogmail-*.noarch.rpm \
    gogmail.cdx.json gogmail.spdx.json grype-report.txt pip-audit.json \
    requirements.lock.txt 2>/dev/null > SHA256SUMS )

echo ">> [6/6] Done. Artifacts in dist/:"
ls -1 dist/
echo ">> SHA256SUMS is signed in CI with keyless cosign (no stored secret)."
