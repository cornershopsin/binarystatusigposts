# binarystatusigposts

Images and captions for the **@binarystatus** Instagram account, plus a GitHub Actions
workflow that publishes new posts through the Instagram Graph API (no third-party scheduler).

> **Status: untested against the live Instagram API.** The script and workflow were tested
> locally against a mock server only. Endpoint names, limits and token requirements were
> written from memory; check them against Meta's current Instagram Content Publishing docs.

## How a post is published

1. Add `posts/<name>.jpg` (JPEG only, 1080x1080 recommended) **and** `posts/<name>.txt` (the caption)
   in the same commit.
2. Push to `main`. The workflow publishes images that were *added* in that push.
3. The script validates the files, checks the public image URL, creates an Instagram media
   container, waits for it to finish, then publishes it.

Existing images are never re-published; only newly added ones are.

## One-time setup (done by the account owner)

1. Meta developer app and access token with publishing permission for the @binarystatus
   professional account. Exact permissions/token type: see Meta's docs.
2. Repository **secrets** (Settings > Secrets and variables > Actions > Secrets):
   - `IG_USER_ID`: the Instagram account ID used by the API
   - `IG_ACCESS_TOKEN`: the access token
3. Optional repository **variables**: `IG_GRAPH_BASE` (default `https://graph.facebook.com`),
   `IG_GRAPH_VERSION` (e.g. `v21.0`; empty = unversioned).
4. Test with a **dry run**: Actions > "Publish to Instagram" > Run workflow, enter an existing
   image path, leave *dry run* ticked. It validates everything and calls no Instagram API.
5. Optionally do one real manual run (untick *dry run*) with a post you are happy to publish.
6. Only then create the repository **variable** `AUTO_PUBLISH` = `true` to enable
   publishing on every push.

## Files

- `scripts/publish_instagram.py`: the publisher (Python standard library only)
- `.github/workflows/publish-instagram.yml`: the workflow
- `posts/`: images and captions
- `test/`: connectivity test image from setup (safe to delete)

## Local dry run

```bash
GITHUB_REPOSITORY=cornershopsin/binarystatusigposts GITHUB_SHA=<a pushed commit sha> \
DRY_RUN=1 python3 scripts/publish_instagram.py posts/2026-10-08-automate-boring-part.jpg
```
