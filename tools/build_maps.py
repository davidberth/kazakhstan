"""Render static story maps: vector basemap, real projections, point markers.

    python tools/build_maps.py            # globe + overview + one per chapter
    python tools/build_maps.py --list     # resolved coordinates, no render
    python tools/build_maps.py --only globe

Output: public/maps/*.png

Vector features from Natural Earth via Cartopy -- no tiles, so no Web Mercator
constraint and no tile-license attribution to carry. Cartopy caches the Natural
Earth shapefiles locally on first run; after that this works offline.

Projections are chosen per map rather than globally:

  globe     Orthographic  -- where on Earth this is. Honest about the sphere:
                             the limb falls off, area shrinks toward the edge.
  overview  AlbersEqualArea -- equal-area conic with standard parallels inset
                             from Kazakhstan's north and south edges. Area is
                             preserved, which is what you want when the eye
                             reads relative size as meaning.
  chapter   LambertConformal -- conformal at local scale, so shapes and angles
                             are right where you are actually looking.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display on CI; must precede pyplot
import matplotlib.pyplot as plt
import cartopy.crs as ccrs
import cartopy.feature as cfeature
import cartopy.io.shapereader as shpreader

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".photo-cache" / "index.json"
OUT_DIR = ROOT / "public" / "maps"

# --------------------------------------------------------------------------- #
# Chapters
# --------------------------------------------------------------------------- #

# Narrative order, which is not the same as chronological order: the Almaty and
# mountains photos interleave by timestamp.
ORDER = ["astana", "shapan", "almaty", "mountains"]

TITLES = {
    "astana": "Astana",
    "shapan": "The Shapan",
    "almaty": "Almaty",
    "mountains": "The Mountains",
}

# Only used when a chapter has no GPS at all. All four chapters currently
# resolve from real EXIF, so these are a safety net rather than the source.
FALLBACK = {
    "astana": (51.1694, 71.4491),
    "shapan": (50.9845, 71.3469),   # steppe southwest of Astana
    "almaty": (43.2380, 76.8829),
    "mountains": (43.13, 77.08),    # Zailiysky Alatau, south of Almaty
}

# Published coordinates are rounded to this many degrees (~5 km). The image
# pipeline strips GPS from the photos; this keeps the maps from putting it back.
PRECISION = 0.05

# Half-extent of a chapter map, in degrees of latitude.
CHAPTER_SPAN_DEG = 0.75

# --------------------------------------------------------------------------- #
# Palette — matches src/styles/global.css
# --------------------------------------------------------------------------- #

BG = "#12151a"
OCEAN = "#161b22"
LAND = "#1e242c"
LAND_FAR = "#191e25"
COAST = "#39424e"
BORDER = "#3d4652"
BORDER_KZ = "#4a90a4"
RIVER = "#2b3f4a"
GRID = "#222932"
POINT = "#d9a441"
INK = "#eceae5"
INK_DIM = "#a8a49b"

KZ_LON, KZ_LAT = 68.0, 48.0


def chapter_coords() -> dict[str, tuple[float, float, str]]:
    """Median GPS per chapter, quantized, with fallbacks.

    Median rather than mean so one photo taken at an airport en route does not
    drag the chapter marker across the country.
    """
    by_group: dict[str, list[tuple[float, float]]] = {}
    if CACHE.exists():
        for entry in json.loads(CACHE.read_text(encoding="utf-8")).values():
            gps = entry.get("gps")
            if gps:
                by_group.setdefault(entry["photo"]["group"], []).append(
                    (gps["lat"], gps["lon"]))

    out = {}
    for group in dict.fromkeys(ORDER + sorted(by_group)):
        if group in by_group:
            lat = statistics.median(p[0] for p in by_group[group])
            lon = statistics.median(p[1] for p in by_group[group])
            source = f"{len(by_group[group])} photo(s)"
        elif group in FALLBACK:
            lat, lon = FALLBACK[group]
            source = "fallback"
        else:
            continue
        out[group] = (
            round(round(lat / PRECISION) * PRECISION, 4),
            round(round(lon / PRECISION) * PRECISION, 4),
            source,
        )
    return out


def new_figure(proj, size=(12, 7.5)):
    fig = plt.figure(figsize=size, dpi=110, facecolor=BG)
    ax = fig.add_subplot(1, 1, 1, projection=proj)
    ax.patch.set_facecolor(OCEAN)
    ax.spines["geo"].set_visible(False)
    return fig, ax


def add_land(ax, scale="50m", far=False):
    ax.add_feature(cfeature.OCEAN.with_scale(scale), facecolor=OCEAN, zorder=0)
    ax.add_feature(cfeature.LAND.with_scale(scale),
                   facecolor=LAND_FAR if far else LAND, zorder=1)
    ax.add_feature(cfeature.LAKES.with_scale(scale), facecolor=OCEAN,
                   edgecolor=COAST, linewidth=0.4, zorder=2)
    ax.add_feature(cfeature.COASTLINE.with_scale(scale),
                   edgecolor=COAST, linewidth=0.6, zorder=3)
    ax.add_feature(cfeature.BORDERS.with_scale(scale),
                   edgecolor=BORDER, linewidth=0.7, zorder=3)


def add_kazakhstan_outline(ax, scale="50m"):
    """Trace the subject country a little brighter than its neighbors."""
    path = shpreader.natural_earth(resolution=scale, category="cultural",
                                   name="admin_0_countries")
    for rec in shpreader.Reader(path).records():
        iso = rec.attributes.get("ADM0_A3") or rec.attributes.get("ISO_A3")
        if iso == "KAZ":
            ax.add_geometries([rec.geometry], ccrs.PlateCarree(),
                              facecolor="none", edgecolor=BORDER_KZ,
                              linewidth=1.3, zorder=4)
            return


def place_labels(ax, ordered, xy):
    """Greedy vertical dodge so labels never sit on top of each other.

    Three of five chapters are within ~80 km of Astana, so at country scale
    their labels collide wherever the markers land. Walk top to bottom, push
    each label clear of the last one it would overlap, and draw a leader line
    wherever a label ended up away from its marker.
    """
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    gap, xgap = (y1 - y0) * 0.052, (x1 - x0) * 0.30

    placed: list[tuple[float, float]] = []
    out = {}
    for g in sorted(ordered, key=lambda g: -xy[g][1]):
        x, y = xy[g]
        ly = y
        while any(abs(ly - py) < gap and abs(x - px) < xgap for px, py in placed):
            ly -= gap
        placed.append((x, ly))
        out[g] = ly
    return out, (x1 - x0) * 0.015


def geodesic_circle(lat, lon, radius_km, n=96):
    """Points on a circle of true ground radius, in lon/lat.

    Drawn on the sphere rather than as a screen-space disc, so the shape carries
    the projection's distortion honestly: a circle looks circular in Albers near
    the standard parallels and visibly elliptical toward the limb of the globe.
    """
    ang = radius_km / 6371.0
    lat1, lon1 = math.radians(lat), math.radians(lon)
    pts = []
    for i in range(n + 1):
        brg = 2 * math.pi * i / n
        lat2 = math.asin(math.sin(lat1) * math.cos(ang)
                         + math.cos(lat1) * math.sin(ang) * math.cos(brg))
        lon2 = lon1 + math.atan2(
            math.sin(brg) * math.sin(ang) * math.cos(lat1),
            math.cos(ang) - math.sin(lat1) * math.sin(lat2),
        )
        pts.append((math.degrees(lon2), math.degrees(lat2)))
    return pts


def draw_regions(ax, proj, coords, ordered, highlight, radius_km, labels=True,
                 route=False):
    """Approximate circular regions rather than points.

    A point implies a GPS fix on a family's home. A soft circle says "somewhere
    around here", which is both truer to how these coordinates were derived
    (a median over a chapter's photos) and the right amount of precision to
    publish.
    """
    xy = {}
    for g in ordered:
        lat, lon, _ = coords[g]
        xy[g] = proj.transform_point(lon, lat, ccrs.PlateCarree())

    for g in ordered:
        lat, lon, _ = coords[g]
        active = highlight is None or g == highlight
        ring = geodesic_circle(lat, lon, radius_km if active else radius_km * 0.72)
        lons, lats = zip(*ring)
        ax.fill(lons, lats, transform=ccrs.PlateCarree(), color=POINT,
                alpha=0.17 if active else 0.08, zorder=5, linewidth=0)
        ax.plot(lons, lats, transform=ccrs.PlateCarree(), color=POINT,
                alpha=0.7 if active else 0.3, lw=1.4 if active else 0.9, zorder=6)

    if route and len(ordered) > 1:
        lons = [coords[g][1] for g in ordered]
        lats = [coords[g][0] for g in ordered]
        # Geodetic transform draws great-circle arcs rather than straight lines
        # in projected space -- the honest path between two points on a sphere.
        ax.plot(lons, lats, transform=ccrs.Geodetic(), color=POINT,
                lw=1.3, alpha=0.45, ls=(0, (5, 4)), zorder=4)

    if not labels:
        return

    label_y, dx = place_labels(ax, ordered, xy)

    for g in ordered:
        x, y = xy[g]
        active = highlight is None or g == highlight
        ly = label_y[g]
        if abs(ly - y) > (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.004:
            ax.plot([x, x + dx * 0.7], [y, ly], color=POINT,
                    lw=0.8, alpha=0.45, zorder=7)
        ax.text(x + dx, ly, TITLES.get(g, g.title()),
                color=INK if active else INK_DIM,
                fontsize=15 if active else 11,
                alpha=1.0 if active else 0.8, zorder=8,
                va="center", ha="left")


def save(fig, path: Path):
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.01)
    fig.savefig(path, facecolor=BG, pad_inches=0.02, bbox_inches="tight")
    plt.close(fig)
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1024:.0f} KB")


# --------------------------------------------------------------------------- #
# Map types
# --------------------------------------------------------------------------- #


def map_globe(coords, ordered) -> tuple:
    """Orthographic: where on Earth this is. The limb is the point."""
    proj = ccrs.Orthographic(central_longitude=KZ_LON, central_latitude=KZ_LAT)
    fig, ax = new_figure(proj, size=(8, 8))
    ax.set_global()
    add_land(ax, "110m", far=True)
    ax.gridlines(color=GRID, linewidth=0.5, alpha=0.7)
    add_kazakhstan_outline(ax, "110m")
    # All four regions, unlabeled. At this scale Astana/shapan and
    # Almaty/mountains each merge into one blob, which is the right reading:
    # the globe answers "where on Earth", not "which chapter".
    draw_regions(ax, proj, coords, ordered, None, radius_km=170, labels=False)
    return fig, ax


def map_overview(coords, ordered) -> tuple:
    """Albers equal-area conic over Kazakhstan, with the journey drawn on."""
    proj = ccrs.AlbersEqualArea(central_longitude=KZ_LON, central_latitude=KZ_LAT,
                                standard_parallels=(45.0, 51.0))
    fig, ax = new_figure(proj)
    ax.set_extent([44.5, 89.5, 39.0, 56.5], crs=ccrs.PlateCarree())
    add_land(ax, "50m")
    ax.add_feature(cfeature.RIVERS.with_scale("50m"), edgecolor=RIVER,
                   linewidth=0.5, zorder=3)
    ax.gridlines(color=GRID, linewidth=0.5, alpha=0.8,
                 xlocs=range(40, 96, 5), ylocs=range(36, 62, 5))
    add_kazakhstan_outline(ax, "50m")
    draw_regions(ax, proj, coords, ordered, None, radius_km=48, route=True)
    return fig, ax


def map_chapter(coords, ordered, chapter) -> tuple:
    """Lambert conformal, local. Shapes and angles true where you're looking."""
    lat, lon, _ = coords[chapter]
    proj = ccrs.LambertConformal(central_longitude=lon, central_latitude=lat,
                                 standard_parallels=(lat - 2, lat + 2))
    fig, ax = new_figure(proj)
    span = CHAPTER_SPAN_DEG
    lon_span = span / max(0.2, math.cos(math.radians(lat))) * (12 / 7.5)
    ax.set_extent([lon - lon_span, lon + lon_span, lat - span, lat + span],
                  crs=ccrs.PlateCarree())
    add_land(ax, "10m")
    ax.add_feature(cfeature.RIVERS.with_scale("10m"), edgecolor=RIVER,
                   linewidth=0.7, zorder=3)
    ax.gridlines(color=GRID, linewidth=0.5, alpha=0.8)
    nearby = [g for g in ordered
              if abs(coords[g][0] - lat) < span and abs(coords[g][1] - lon) < lon_span]
    draw_regions(ax, proj, coords, nearby or [chapter], chapter, radius_km=11)
    return fig, ax


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print coordinates and exit")
    ap.add_argument("--only", help="render one map: globe, overview, or a chapter")
    args = ap.parse_args()

    coords = chapter_coords()
    if not coords:
        print("no chapter coordinates — add photos with GPS, or edit FALLBACK")
        return 1

    if args.list:
        print(f"{'chapter':12} {'lat':>8} {'lon':>8}  source")
        for g, (lat, lon, src) in coords.items():
            print(f"{g:12} {lat:8.2f} {lon:8.2f}  {src}")
        return 0

    ordered = [g for g in ORDER if g in coords]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    jobs = {"globe": lambda: map_globe(coords, ordered),
            "overview": lambda: map_overview(coords, ordered)}
    for g in ordered:
        jobs[g] = (lambda g=g: map_chapter(coords, ordered, g))

    if args.only:
        if args.only not in jobs:
            print(f"unknown map '{args.only}'; choose from {', '.join(jobs)}")
            return 1
        jobs = {args.only: jobs[args.only]}

    for name, build in jobs.items():
        fig, _ = build()
        save(fig, OUT_DIR / f"{name}.png")

    print(f"\n{len(jobs)} maps -> {OUT_DIR.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
