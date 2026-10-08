#!/usr/bin/env python3
"""Publish JPEG images from posts/ to Instagram via the Instagram Graph API.

For each image path given, this script:
  1. checks the file is a real JPEG and that a caption file sits next to it
     (posts/foo.jpg  ->  posts/foo.txt),
  2. builds a public, commit-pinned raw.githubusercontent.com URL for the image
     and confirms it is reachable and served as image/jpeg,
  3. creates a media container, waits for it to finish processing, publishes it.

Configuration (environment variables):
  IG_USER_ID         Instagram professional account ID (required unless DRY_RUN)
  IG_ACCESS_TOKEN    access token with publishing permission (required unless DRY_RUN)
  IG_GRAPH_BASE      API host, default https://graph.facebook.com
  IG_GRAPH_VERSION   API version such as "v21.0"; empty = unversioned call
  GITHUB_REPOSITORY  owner/repo   (set automatically by GitHub Actions)
  GITHUB_SHA         commit SHA   (set automatically by GitHub Actions)
  DRY_RUN            "1" = validate everything and print the plan, call no Meta API

NOTE: endpoint paths, field names, limits and status values below are written
from memory of Meta's Instagram Content Publishing docs and have NOT been
tested against the live API. Verify them against Meta's current documentation.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH_BASE = os.environ.get("IG_GRAPH_BASE", "https://graph.facebook.com").rstrip("/")
GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "").strip().strip("/")

MAX_CAPTION_CHARS = 2200          # from memory; verify in Meta docs
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # from memory; verify in Meta docs
POSTS_DIR = "posts"


class PublishError(Exception):
    pass


def mask(text, token):
    """Never let the access token reach logs."""
    return text.replace(token, "***") if token else text


def api_url(path):
    parts = [GRAPH_BASE]
    if GRAPH_VERSION:
        parts.append(GRAPH_VERSION)
    parts.append(path.strip("/"))
    return "/".join(parts)


def call(method, path, params, token):
    """Call the Graph API; return parsed JSON or raise PublishError."""
    body = dict(params)
    body["access_token"] = token
    encoded = urllib.parse.urlencode(body)
    if method == "POST":
        req = urllib.request.Request(api_url(path), data=encoded.encode(), method="POST")
    else:
        req = urllib.request.Request(f"{api_url(path)}?{encoded}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        raise PublishError(mask(f"HTTP {e.code} from {method} {path}: {raw[:800]}", token))
    except urllib.error.URLError as e:
        raise PublishError(mask(f"Network error on {method} {path}: {e.reason}", token))
    try:
        data = json.loads(raw)
    except ValueError:
        raise PublishError(mask(f"Non-JSON response from {method} {path}: {raw[:300]}", token))
    if isinstance(data, dict) and "error" in data:
        raise PublishError(mask(f"API error on {method} {path}: {json.dumps(data['error'])[:800]}", token))
    return data


def validate_image(path_str):
    """Return (path, caption). Raise PublishError if anything is wrong."""
    rel = Path(path_str)
    if rel.is_absolute() or ".." in rel.parts or rel.parts[:1] != (POSTS_DIR,):
        raise PublishError(f"{path_str}: path must be inside {POSTS_DIR}/")
    if rel.suffix.lower() not in (".jpg", ".jpeg"):
        raise PublishError(f"{path_str}: only .jpg/.jpeg files are published (PNG is not used)")
    if not rel.is_file():
        raise PublishError(f"{path_str}: file not found")
    size = rel.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise PublishError(f"{path_str}: {size} bytes exceeds {MAX_IMAGE_BYTES}")
    with open(rel, "rb") as f:
        if f.read(3) != b"\xff\xd8\xff":
            raise PublishError(f"{path_str}: not a valid JPEG (bad header)")
    cap_path = rel.with_suffix(".txt")
    if not cap_path.is_file():
        raise PublishError(f"{path_str}: caption file {cap_path} is missing; refusing to post without one")
    caption = cap_path.read_text(encoding="utf-8").strip()
    if not caption:
        raise PublishError(f"{cap_path}: caption is empty")
    if len(caption) > MAX_CAPTION_CHARS:
        raise PublishError(f"{cap_path}: caption is {len(caption)} chars, limit {MAX_CAPTION_CHARS}")
    return rel, caption


def public_url(rel):
    repo = os.environ.get("GITHUB_REPOSITORY")
    sha = os.environ.get("GITHUB_SHA")
    if not repo or not sha:
        raise PublishError("GITHUB_REPOSITORY and GITHUB_SHA must be set to build the image URL")
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{urllib.parse.quote(rel.as_posix())}"


def check_url(url, attempts=6, delay=5):
    """The image must be publicly fetchable as image/jpeg before Instagram is asked to fetch it."""
    last = "unknown"
    for i in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=30) as resp:
                ctype = resp.headers.get("Content-Type", "")
                head = resp.read(3)
            if resp.status == 200 and ctype.startswith("image/jpeg") and head == b"\xff\xd8\xff":
                return
            last = f"status {resp.status}, content-type {ctype!r}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except urllib.error.URLError as e:
            last = f"network error: {e.reason}"
        if i < attempts - 1:
            time.sleep(delay)
    raise PublishError(f"image URL is not publicly reachable as a JPEG ({last}): {url}")


def wait_until_ready(container_id, token, timeout=180, interval=5):
    deadline = time.time() + timeout
    while True:
        status = call("GET", container_id, {"fields": "status_code"}, token).get("status_code")
        print(f"  container {container_id}: {status}")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise PublishError(f"container {container_id} ended with status {status}")
        if time.time() > deadline:
            raise PublishError(f"container {container_id} not ready after {timeout}s (last status {status})")
        time.sleep(interval)


def publish_one(path_str, dry_run, ig_user_id, token):
    rel, caption = validate_image(path_str)
    url = public_url(rel)
    print(f"{rel}: caption {len(caption)} chars")
    print(f"  image URL: {url}")
    check_url(url)
    print("  image URL reachable as image/jpeg")
    if dry_run:
        print("  DRY_RUN: would create container, wait, and publish. No Meta API calls made.")
        return
    container = call("POST", f"{ig_user_id}/media", {"image_url": url, "caption": caption}, token)["id"]
    print(f"  container created: {container}")
    wait_until_ready(container, token)
    media_id = call("POST", f"{ig_user_id}/media_publish", {"creation_id": container}, token)["id"]
    print(f"  PUBLISHED media id {media_id}")
    try:  # best effort only
        link = call("GET", media_id, {"fields": "permalink"}, token).get("permalink")
        if link:
            print(f"  permalink: {link}")
    except PublishError:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", help="paths like posts/2026-10-08-example.jpg")
    args = ap.parse_args()

    dry_run = os.environ.get("DRY_RUN", "0") == "1"
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not dry_run and not (ig_user_id and token):
        print("IG_USER_ID and IG_ACCESS_TOKEN are required (or set DRY_RUN=1)", file=sys.stderr)
        return 2

    failures = 0
    for path_str in args.images:
        try:
            publish_one(path_str, dry_run, ig_user_id, token)
        except PublishError as e:
            failures += 1
            print(f"FAILED: {e}", file=sys.stderr)
    print(f"done: {len(args.images) - failures} ok, {failures} failed" + (" (dry run)" if dry_run else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
