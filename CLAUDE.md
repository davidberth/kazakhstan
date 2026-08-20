# lixel.io/kazakhstan — Kazakhstan photo story

A story-driven photo album from a week in Kazakhstan (August 2026), visiting my wife's
extended family. Chronological narrative with photos, not a gallery grid.

**Live at <https://lixel.io/kazakhstan/>.** Four chapters, 86 photos, bilingual
English/Kazakh prose. Aizhan is David's wife; her family hosted the trip.

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

The site is public and the repo is public, so every photo is browsable at
`github.com/davidberth/kazakhstan` as well as on the site. `noindex` and `robots.txt`
prevent search-engine discovery, which covers the realistic case. That is obscurity,
not protection. Do not describe the album as private or hidden.

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

**Mail runs on this domain.** Never touch MX, SPF, DKIM, or DMARC records. No DNS
change is needed for this project as it stands.

The site is served from the GitHub Pages **project path**, not a subdomain.

A `kz.lixel.io` subdomain was scoped and then dropped: it needed a DNS record at
Squarespace, and the project path was judged good enough for a small personal album.
`public/CNAME` was removed accordingly. **The DNS record was never created and
`kz.lixel.io` does not resolve** — do not reference it as if it works.

Because it is a project site, everything is served under `/kazakhstan`. This is the
single most common source of broken links here:

- `astro.config.mjs` sets `site: 'https://lixel.io'` and `base: '/kazakhstan'`.
- `BASE_URL` carries **no trailing slash** in this Astro version. Never build an asset
  URL by string concatenation; always use `asset()` from `src/lib/url.js`.
- The photo manifest stores **relative** paths (`photos/foo.webp`) for the same reason.

Moving to a subdomain later is a change to `base` plus a DNS record, nothing else.

## Architecture

Two decoupled halves. This separation is deliberate — the framework can be swapped without
touching the pipeline, and vice versa.

```
photos/originals/     full-res JPEGs and clips, NOT committed (gitignored)
  |
  |  tools/build_photos.py   — EXIF read, orient, resize, encode, hash, manifest
  |  tools/video.py          — probe, proxy-decode, pick loop window, encode
  v
public/photos/        committed web derivatives (multiple widths, WebP)
public/video/         committed muted H.264 loops
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
- Emit derivatives at `WIDTHS = (480, 800, 1200, 1800)`, WebP only. A 2400 tier was
  removed: it was 46% of total weight for a size only a 4K display viewing full-screen
  would request. Committed derivatives are ~48 MB for 86 photos.
- Recover a timestamp from the filename when EXIF has none (WhatsApp strips EXIF but
  writes the date into the name). `taken_from` records which source was used.
- Emit a tiny inline placeholder (blurhash or a ~20px base64 JPEG) so layout doesn't jump.
- Record intrinsic dimensions in the manifest — every `<img>` needs `width`/`height` to
  avoid cumulative layout shift.
- **Deterministic and idempotent.** Content-hashed output names; unchanged input produces
  byte-identical output and no git churn. Re-encoding photos on every run would bloat the
  repo permanently, since git keeps every version of every binary forever.

### Video loops

A clip in `photos/originals/` becomes a short muted loop. **A loop is a photo
that moves:** it carries the same poster, dimensions, and placeholder as a
still, so grid layout, chronological ordering, and the lightbox all work on it
unchanged. The site renders `<video>` instead of `<img>` when a manifest entry
has a `loop` key.

- Needs an **ffmpeg build**, not a Python package. `tools/video.py` finds it on
  PATH, at `C:/ffmpeg/bin`, or via `FFMPEG_DIR`. No ffmpeg means clips are
  skipped with a warning and stills still build.
- Which seconds to loop is chosen by analysing a 160px gray proxy piped from
  ffmpeg into numpy: reward motion and sharpness, reward first/last frame
  similarity (loop closure), reject windows containing a scene cut, and require
  a motion floor so a static window does not win by collecting free marks.
- Scene cuts are detected as spikes **relative to the local median**, never as a
  fraction of the global maximum — a fast pan is sustained high motion and would
  otherwise read as an unbroken run of cuts.
- Encode is muted on purpose: browsers only autoplay muted video, so the audio
  track could never play and is pure weight. `yuv420p` + `profile high` is what
  makes the file decodable everywhere.
- Loops play only while on screen (`IntersectionObserver`) and never under
  `prefers-reduced-motion`. `preload="none"` means an off-screen loop costs
  nothing.
- **Video is where the GitHub Pages limits become reachable.** Photos never will
  be. Watch total committed size; a few minutes of loops is fine, a library of
  full clips is not.

### Content

Story text lives in Markdown (Astro content collections) at `src/content/chapters/`,
one file per chapter. Photos join to chapters by the `group` key, which is the source
folder name under `photos/originals/` with any numeric prefix stripped — chapters do not
list photo IDs individually.

Each chapter carries Kazakh translations in frontmatter (`title_kk`, `place_kk`,
`lead_kk`, `body_kk`) rendered beside the English, with `lang="kk"` on the elements.
**The Kazakh is machine-produced and was flagged to David for a native reader's review.**
Chapters: astana, shapan, almaty, mountains.

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
python tools/build_maps.py              # regenerate chapter maps
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
