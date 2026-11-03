#!/usr/bin/env python3
"""Read-only discovery and exact-version vendor release notes (standard library)."""

import argparse
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
import urllib.request

IMAGE = "docker.io/aidenzou/i-notebook"
TAG_API = "https://hub.docker.com/v2/repositories/aidenzou/i-notebook/tags?page_size=100"
NOTES_URL = "https://notebook.xiguabyte.com/s/cme25e18g0003qp01q8np7oix"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}")


def fetch(url):
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "lazycat-i-notebook-adapter/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                result = response.read(5 * 1024 * 1024 + 1)
            if len(result) > 5 * 1024 * 1024:
                raise ValueError("Upstream metadata exceeds size limit")
            return result
        except OSError:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def latest_candidate():
    candidates = []
    url = TAG_API
    for _ in range(20):
        document = json.loads(fetch(url))
        for tag in document["results"]:
            if not VERSION.fullmatch(tag["name"]):
                continue
            images = [i for i in tag["images"] if i.get("os") == "linux" and i.get("architecture") == "amd64"]
            if len(images) != 1 or not DIGEST.fullmatch(images[0].get("digest", "")):
                continue
            candidates.append((tuple(map(int, tag["name"].split("."))), tag["name"], images[0]["digest"]))
        url = document.get("next")
        if not url:
            break
        if not url.startswith("https://hub.docker.com/v2/"):
            raise ValueError("Unexpected Docker Hub pagination destination")
    else:
        raise ValueError("Docker Hub tag pagination exceeded limit")
    if not candidates:
        raise ValueError("No stable linux/amd64 release found")
    _, version, digest = max(candidates)
    return {"version": version, "ref": f"{IMAGE}:{version}", "digest": digest}


class ReleaseParser(HTMLParser):
    """Only read main content, between exact version h2 headings.

    SSR pages can repeat content in hydration scripts. Ignoring everything
    outside <main> and script/style avoids both duplicate and unrelated notes.
    """

    def __init__(self, version):
        super().__init__(convert_charrefs=True)
        self.version = version
        self.main = False
        self.skip = 0
        self.heading = None
        self.active = False
        self.matches = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.main = True
        if not self.main:
            return
        if tag in ("script", "style"):
            self.skip += 1
        if self.skip:
            return
        if tag == "h2":
            self.active = False
            self.heading = []
        if self.active:
            if tag in ("p", "h3", "h4", "br", "ul", "ol"):
                self.parts.append("\n")
            elif tag == "li":
                self.parts.append("\n- ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
            return
        if not self.main or self.skip:
            return
        if tag == "h2" and self.heading is not None:
            self.active = "".join(self.heading).strip() == self.version
            self.matches += int(self.active)
            self.heading = None
        if self.active and tag in ("p", "li", "h3", "h4"):
            self.parts.append("\n")
        if tag == "main":
            self.main = self.active = False

    def handle_data(self, data):
        if not self.main or self.skip:
            return
        if self.heading is not None:
            self.heading.append(data)
        elif self.active:
            self.parts.append(data)


def release_notes(html, version):
    if not VERSION.fullmatch(version):
        raise ValueError("Expected a stable x.y.z version")
    parser = ReleaseParser(version)
    parser.feed(html)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    text = "\n".join(line for line in lines if line)
    if parser.matches != 1 or not text or len(text) > 16000:
        raise ValueError(f"Missing, duplicated or invalid release notes for {version}")
    return f"iNotebook {version}\n\n{text}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("candidate", "changelog"))
    args = parser.parse_args()
    if args.operation == "candidate":
        print(json.dumps(latest_candidate()))
    else:
        version = os.environ.get("LAZYCAT_SOURCE_VERSION", "")
        tag = os.environ.get("LAZYCAT_SOURCE_TAG", version)
        if tag != version:
            raise ValueError("Selected Docker tag and source version disagree")
        print(release_notes(fetch(NOTES_URL).decode("utf-8"), version))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        print(f"iNotebook upstream validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
