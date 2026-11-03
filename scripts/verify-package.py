"""Fail packaging unless this exact upstream digest passed all runtime tests."""

import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent.parent
CHECKS = ['legacy-schema', 'notes', 'hierarchy', 'history', 'settings', 'api-auth', 'attachments', 'fresh-install', 'sqlite', 'migration-backup', 'restart']


def verify(root):
    proof = json.loads((root / ".lazycat-action/upgrade-proof.json").read_text())
    manifest = (root / "lzc-manifest.yml").read_text()
    package = (root / "package.yml").read_text()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", proof["digest"]):
        raise ValueError("Invalid tested platform digest")
    expected = f"docker.io/aidenzou/i-notebook:{proof['source_version']}@{proof['digest']}"
    pins = re.findall(r"^\s*# upstream: (\S+)\s*$", manifest, re.MULTILINE)
    if pins != [expected]:
        raise ValueError("Delivered upstream image is different from the tested digest")
    version = re.search(r"^version:\s*(\S+)\s*$", package, re.MULTILINE)
    if not version or version[1] != proof["application_version"]:
        raise ValueError("Package version differs from tested application version")
    if proof.get("checks") != CHECKS or proof.get("baseline_source_version") != "0.7.1":
        raise ValueError("Incomplete upgrade and initialization evidence")
    if not re.search(r"^\s+image: registry\.lazycat\.cloud/\S+\s*$", manifest, re.MULTILINE):
        raise ValueError("Official image copy has not completed")
    for required in ("/lzcapp/var/data:/appdata", "/lzcapp/var/data/uploads:/app/uploads", "DATABASE_URL=file:/appdata/db.sqlite", "SITE_URL=https://{{ .S.AppDomain }}", "/uploads/=http://notebook.com.luck.lazycat.notebook.lzcapp:3000/api/file/"):
        if required not in manifest:
            raise ValueError("Missing persistence, domain or legacy attachment configuration: " + required)


if __name__ == "__main__":
    verify(ROOT)
    print("Verified the final package uses the tested digest and preserves the data contract.")
