# kz.lixel.io — Kazakhstan photo story

A story-driven photo album from a week in Kazakhstan (August 2026), visiting my wife's
extended family. Chronological narrative with photos, not a gallery grid. Published at
`https://kz.lixel.io`.

## Who you're working with

David Berthiaume — 25 years in computer vision, geospatial, AI, data science, HPC, and
computer graphics. Master's degrees from Harvard and WPI. First-author publications,
8 patents, sold a startup. **Essentially zero web development experience** beyond building
`lixel.io` as a Jekyll site with a custom theme deployed via GitHub Actions.

What this means in practice:

- **Explain web-specific things.** CSS layout models, the cascade, how the browser paints,
  what a build step actually does, why bundlers exist, DNS propagation, TLS issuance. These
  are genuinely new. Explain the *model*, not just the incantation — reasoning from a
  mental model is how he works.
- **Do not explain fundamentals.** Python, NumPy, image processing, color spaces, EXIF,
  linear algebra, GPU/parallelism, complexity, data structures, statistics. Assume expert
  fluency and skip the preamble entirely.
- **Prefer Python for anything algorithmic.** Image processing, EXIF handling, ordering,
  dedupe, and derivative generation belong in Python where he is fastest and strongest.
  The web layer should stay thin and mostly declarative.
- **State recommendations, don't survey options.** He'll push back if he disagrees.

## Locked decisions

| Concern | Decision | Why |
|---|---|---|
| Host | **GitHub Pages** | Already runs `lixel.io` this way; nothing new to learn or break |
| Framework | **Astro** | Static output, ~zero JS shipped, good fit for scroll-driven story; worth learning |
| Images | **Python pipeline** (`pyvips`/`Pillow`) | Plays to his strengths; keeps web layer trivial and host-agnostic |
| Repo | Public (Pages free tier requires it) | Privacy is not a concern for this project |
| Discovery | `noindex` + not linked from `lixel.io` | Keeps it out of search results |
| Source photos | A few hundred JPEGs, Android/camera | |

### Honest note on privacy

The site is public and the repo is public. `kz.lixel.io` will appear in public Certificate
Transparency logs as soon as Pages issues a cert, so the hostname is discoverable — this is
obscurity, not protection. `noindex` prevents search-engine discovery, which covers the
realistic case. Do not describe the album as private or hidden.

## Infrastructure facts

Verified 2026-08-17:

```
lixel.io          A      185.199.108-111.153     (GitHub Pages)
www.lixel.io      CNAME  davidberth.github.io
lixel.io          MX     smtp.google.com         (Google Workspace)
lixel.io          TXT    v=spf1 include:_spf.google.com ~all
lixel.io          NS     ns-cloud-b{1..4}.googledomains.com
```

- **Registrar:** Squarespace (auto-renews 2026-08-31)
- **Nameservers:** `ns-cloud-b*.googledomains.com` — legacy Google Domains infrastructure
  that **Squarespace inherited** in the acquisition. Despite the `googledomains.com` names,
  this is *not* Google Cloud DNS: neither GCP project (`atlas-495720`,
  `mystical-sphinx-495720-v6`) has ever enabled the Cloud DNS API, and `gcloud dns` cannot
  see the zone. **DNS records are edited in the Squarespace domains dashboard.**
- Zone TTL is 300s, so record changes take effect within minutes.
- Existing site repo: `davidberth/davidberth.github.io` (Jekyll, public)

**Mail runs on this domain.** Never touch MX, SPF, DKIM, or DMARC records. Adding
`kz` is a single additive CNAME and must not modify anything else in the zone.

To publish: `kz.lixel.io` → CNAME → `davidberth.github.io`, plus a `CNAME` file containing
`kz.lixel.io` at the site root (Astro: `public/CNAME` — already in place).

**Current state:** Pages is enabled (`build_type: workflow`) and the site deploys cleanly,
but GitHub has not registered the custom domain (`cname: null`) because `kz.lixel.io` does
not resolve yet. Until the Squarespace CNAME record is added, the site serves from the
fallback project path: <https://lixel.io/kz.lixel.io/>. Once DNS resolves, re-run the
deploy (or `gh api --method PUT repos/davidberth/kz.lixel.io/pages -f cname=kz.lixel.io`)
and then enforce HTTPS.

## Architecture

Two decoupled halves. This separation is deliberate — the framework can be swapped without
touching the pipeline, and vice versa.

```
photos/originals/     full-res JPEGs, NOT committed (gitignored)
  |
  |  tools/build_photos.py   — EXIF read, orient, resize, encode, hash, manifest
  v
public/photos/        committed web derivatives (multiple widths, WebP + JPEG)
src/data/photos.json  committed manifest: dimensions, timestamps, placeholders
  |
  |  Astro reads the manifest, emits <picture> with srcset
  v
dist/                 static output -> GitHub Actions -> GitHub Pages
```

### Image pipeline contract

`tools/build_photos.py` owns everything about pixels:

- Read EXIF for capture timestamp (chronological story order) and orientation (apply it,
  then strip the tag).
- **Strip GPS and identifying EXIF** from derivatives. Originals keep it; the web copies
  should not carry location data for someone else's family home.
- Emit derivatives at a fixed set of widths, WebP plus JPEG fallback.
- Emit a tiny inline placeholder (blurhash or a ~20px base64 JPEG) so layout doesn't jump.
- Record intrinsic dimensions in the manifest — every `<img>` needs `width`/`height` to
  avoid cumulative layout shift.
- **Deterministic and idempotent.** Content-hashed output names; unchanged input produces
  byte-identical output and no git churn. Re-encoding photos on every run would bloat the
  repo permanently, since git keeps every version of every binary forever.

### Content

Story text lives in Markdown (Astro content collections), one file per chapter, with
frontmatter referencing photo IDs from the manifest. Prose and layout stay separate from
image machinery.

## Repo conventions

- **Never commit originals.** `photos/originals/` is gitignored. Full-res files belong in
  durable storage (Drive/backup), not git. Only optimized derivatives are committed.
- Committed derivatives should total well under ~200 MB. GitHub Pages recommends a
  published site under 1 GB with a 100 GB/month soft bandwidth cap.
- Do not rewrite git history to purge large files without asking — it breaks the remote.
- Python: standard library plus `pyvips`/`Pillow`; keep dependencies minimal and pinned.

## Commands

```powershell
npm run dev      # Astro dev server, http://localhost:4321 (live reload)
npm run build    # static build -> dist/
npm run preview  # serve dist/ as it will actually be served

python tools/build_photos.py            # regenerate derivatives + manifest
python tools/build_photos.py --force    # ignore cache, re-encode everything
```

## Guardrails

- Don't add client-side JavaScript frameworks. This is a static photo story; Astro ships
  no JS by default and it should stay that way. Small vanilla JS for a lightbox is fine.
- Don't add analytics, tracking, fonts loaded from third-party CDNs, or anything that
  phones home. Self-host assets.
- Don't touch the `davidberth.github.io` repo or any existing DNS record.
- Keep the site host-agnostic: no GitHub-Pages-specific hacks, so moving to another host
  later is a DNS change rather than a rewrite.
- Photos of other people's family. Be conservative with metadata and don't add sharing
  widgets, comment systems, or anything that invites an audience.
