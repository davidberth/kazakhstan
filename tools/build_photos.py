"""Build web derivatives and a manifest from full-resolution originals.

This script owns everything about pixels. The Astro site never touches an
original; it reads `src/data/photos.json` and emits markup around whatever this
produced.

    photos/originals/**/*.jpg   ->   public/photos/<id>-<w>-<hash>.webp
                                     src/data/photos.json

Design constraints:

* Deterministic. Identical input bytes produce identical output bytes and an
  identical manifest. Anything else means spurious git churn, and since git
  stores every version of every binary forever, churn is permanent.
* Idempotent. A second run with no new photos does no work and writes nothing.
* Privacy-preserving. Derivatives are constructed fresh and carry no metadata
  except a color profile. See `_encode`.

Usage:
    python tools/build_photos.py
    python tools/build_photos.py --force      # re-encode everything
    python tools/build_photos.py --jobs 4     # limit parallelism
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as futures
import dataclasses
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "photos" / "originals"
OUT_DIR = ROOT / "public" / "photos"
MANIFEST = ROOT / "src" / "data" / "photos.json"
CACHE = ROOT / ".photo-cache" / "index.json"

SOURCE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# Rendered widths in CSS pixels. The browser picks one from the srcset based on
# viewport width and device pixel ratio, so a 2x phone at 400px CSS uses 800.
# Widths larger than the source are skipped -- upscaling adds bytes, not detail.
WIDTHS = (480, 800, 1200, 1800, 2400)

WEBP_QUALITY = 82        # visually transparent for photographic content
WEBP_METHOD = 6          # 0-6; higher is slower to encode, smaller output
PLACEHOLDER_WIDTH = 16   # inline blur-up preview, ~500 bytes as base64

# EXIF tag numbers (see Exif 2.32 spec).
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME_DIGITIZED = 36868
TAG_DATETIME = 306
TAG_SUBSEC_ORIGINAL = 37521
IFD_GPS = 34853


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class Photo:
    """One source image and everything the site needs to render it."""

    id: str
    group: str
    source: str          # repo-relative, for provenance only; never published
    taken: str | None    # ISO 8601, or None if the camera wrote no timestamp
    sort_key: str
    width: int           # intrinsic dimensions AFTER orientation is applied
    height: int
    placeholder: str     # data: URI, inlined to prevent layout shift
    srcset: list[dict]   # [{"w": 800, "src": "photos/foo-800-ab12cd34.webp"}]

    def to_manifest(self) -> dict:
        d = dataclasses.asdict(self)
        d.pop("source")   # keep original filenames out of the published site
        d.pop("sort_key")
        return d


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    return re.sub(r"[-\s_]+", "-", text) or "photo"


def group_slug(folder: str) -> str:
    """Directory name -> chapter key, dropping any ordering prefix.

    "03-shapan" and "shapan" both yield "shapan". The numeric prefix exists so
    the folders sort sensibly in a file browser; chapter *order* is declared in
    the Markdown frontmatter, which is the single source of truth for it.
    """
    return slugify(re.sub(r"^\d+[-_\s]*", "", folder))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def parse_exif_datetime(exif) -> tuple[str | None, str]:
    """Return (ISO timestamp or None, sort key).

    Cameras write "YYYY:MM:DD HH:MM:SS". Subsecond is a separate ASCII tag and
    matters for burst sequences, where whole-second resolution ties.
    """
    raw = None
    for tag in (TAG_DATETIME_ORIGINAL, TAG_DATETIME_DIGITIZED, TAG_DATETIME):
        value = exif.get(tag)
        if value:
            raw = str(value).strip()
            break

    if not raw:
        return None, "9999"  # undated photos sort last, then by filename

    try:
        stamp = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None, "9999"

    subsec = str(exif.get(TAG_SUBSEC_ORIGINAL, "0")).strip() or "0"
    subsec = re.sub(r"\D", "", subsec)[:3].ljust(3, "0")
    return stamp.isoformat(), f"{stamp.isoformat()}.{subsec}"


def extract_gps(exif) -> dict | None:
    """Read GPS into the local cache only. Never written to the manifest.

    Kept because it is cheap to capture now and expensive to recover later --
    useful if a map view is ever wanted. The cache is gitignored, so these
    coordinates do not leave the machine.
    """
    try:
        gps = exif.get_ifd(IFD_GPS)
    except Exception:
        return None
    if not gps:
        return None

    def dms(values, ref, negative) -> float | None:
        try:
            d, m, s = (float(v) for v in values)
        except (TypeError, ValueError):
            return None
        deg = d + m / 60 + s / 3600
        return -deg if str(ref).upper() == negative else deg

    lat = dms(gps.get(2), gps.get(1, "N"), "S")
    lon = dms(gps.get(4), gps.get(3, "E"), "W")
    if lat is None or lon is None:
        return None
    return {"lat": round(lat, 6), "lon": round(lon, 6)}


def to_srgb(img: Image.Image) -> Image.Image:
    """Convert to sRGB if the file carries a different working profile.

    Phones increasingly tag Display P3. Browsers honor an embedded profile, but
    normalizing once at build time means every derivative is unambiguous and we
    can drop the profile bytes from small sizes.
    """
    profile = img.info.get("icc_profile")
    if not profile:
        return img
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
        dst = ImageCms.createProfile("sRGB")
        return ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
    except Exception:
        # A malformed profile should degrade to "assume sRGB", not crash a build.
        return img.convert("RGB")


def _encode(img: Image.Image, width: int) -> bytes:
    """Resize to `width` and encode WebP with no metadata whatsoever.

    Metadata is stripped by construction rather than by enumeration: Pillow does
    not copy EXIF unless explicitly asked, so a freshly encoded buffer carries
    no GPS, no camera serial, no owner field, and no embedded thumbnail. A
    blacklist would eventually miss a tag; this cannot.
    """
    height = max(1, round(img.height * width / img.width))
    resized = img.resize((width, height), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    resized.save(
        buf,
        format="WEBP",
        quality=WEBP_QUALITY,
        method=WEBP_METHOD,
    )
    return buf.getvalue()


def make_placeholder(img: Image.Image) -> str:
    """A ~500 byte inline preview, shown while the real image loads.

    Inlined as a data: URI so it costs no extra request. Its job is to hold the
    correct aspect ratio and average color so the page does not reflow when
    photos arrive -- the layout shift that makes image-heavy pages feel broken.
    """
    height = max(1, round(img.height * PLACEHOLDER_WIDTH / img.width))
    tiny = img.resize((PLACEHOLDER_WIDTH, height), Image.Resampling.BILINEAR)
    buf = io.BytesIO()
    tiny.save(buf, format="JPEG", quality=40, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


# --------------------------------------------------------------------------- #
# Per-image work (runs in a worker process)
# --------------------------------------------------------------------------- #


def process(path: Path) -> tuple[Photo, dict]:
    rel = path.relative_to(SRC_DIR)
    group = group_slug(rel.parts[0]) if len(rel.parts) > 1 else "unsorted"
    digest = sha256_file(path)
    short = digest[:8]
    # Replace the path separator first; slugify strips punctuation, which would
    # otherwise fuse "almaty/DSC_0101" into "almatydsc-0101".
    photo_id = slugify(rel.with_suffix("").as_posix().replace("/", "-"))

    with Image.open(path) as raw:
        # Apply the EXIF orientation flag to actual pixels, then forget it.
        # Browsers have historically disagreed about honoring the tag; baking
        # the rotation in removes the question.
        img = ImageOps.exif_transpose(raw)
        exif = raw.getexif()
        taken, sort_key = parse_exif_datetime(exif)
        gps = extract_gps(exif)

        img = to_srgb(img)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        widths = [w for w in WIDTHS if w <= img.width] or [min(WIDTHS[0], img.width)]
        if img.width not in widths and img.width < WIDTHS[-1]:
            widths.append(img.width)   # never lose detail the source actually has
        widths = sorted(set(widths))

        srcset = []
        for w in widths:
            name = f"{photo_id}-{w}-{short}.webp"
            (OUT_DIR / name).write_bytes(_encode(img, w))
            srcset.append({"w": w, "src": f"photos/{name}"})

        photo = Photo(
            id=photo_id,
            group=group,
            source=rel.as_posix(),
            taken=taken,
            sort_key=f"{sort_key}|{rel.as_posix()}",
            width=img.width,
            height=img.height,
            placeholder=make_placeholder(img),
            srcset=srcset,
        )

    return photo, {"sha256": digest, "gps": gps, "photo": dataclasses.asdict(photo)}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def discover() -> list[Path]:
    if not SRC_DIR.exists():
        return []
    return sorted(
        p for p in SRC_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in SOURCE_SUFFIXES
    )


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-encode everything")
    ap.add_argument("--jobs", type=int, default=os.cpu_count(), help="worker processes")
    ap.add_argument("--keep-orphans", action="store_true",
                    help="do not delete derivatives whose source is gone")
    args = ap.parse_args()

    sources = discover()
    if not sources:
        print(f"No source images under {SRC_DIR.relative_to(ROOT)}/")
        print("Drop full-resolution JPEGs there (subfolders become groups) and re-run.")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)

    cache = {} if args.force else load_cache()
    photos: list[Photo] = []
    todo: list[Path] = []

    for path in sources:
        key = path.relative_to(SRC_DIR).as_posix()
        entry = cache.get(key)
        # Hash the source rather than trusting mtime: copying files off a camera
        # rewrites mtime constantly and would invalidate the whole cache.
        if entry and entry.get("sha256") == sha256_file(path):
            if all((OUT_DIR / Path(s["src"]).name).exists()
                   for s in entry["photo"]["srcset"]):
                photos.append(Photo(**entry["photo"]))
                continue
        todo.append(path)

    print(f"{len(sources)} source images, {len(todo)} to process, "
          f"{len(sources) - len(todo)} cached")

    if todo:
        with futures.ProcessPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            for i, (photo, entry) in enumerate(pool.map(process, todo), 1):
                photos.append(photo)
                cache[Path(entry["photo"]["source"]).as_posix()] = entry
                print(f"  [{i}/{len(todo)}] {photo.id}  "
                      f"{photo.width}x{photo.height}  {len(photo.srcset)} sizes")

    photos.sort(key=lambda p: p.sort_key)

    MANIFEST.write_text(
        json.dumps(
            {
                "count": len(photos),
                "widths": list(WIDTHS),
                "photos": [p.to_manifest() for p in photos],
            },
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    if not args.keep_orphans:
        live = {Path(s["src"]).name for p in photos for s in p.srcset}
        removed = 0
        for stale in OUT_DIR.glob("*.webp"):
            if stale.name not in live:
                stale.unlink()
                removed += 1
        if removed:
            print(f"pruned {removed} orphaned derivative(s)")

    total = sum(f.stat().st_size for f in OUT_DIR.glob("*.webp"))
    print(f"\n{len(photos)} photos -> {MANIFEST.relative_to(ROOT)}")
    print(f"{OUT_DIR.relative_to(ROOT)}/ is {total / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
