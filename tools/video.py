"""Video helpers: probe, proxy-decode, loop selection, encode.

No Python video library. ffmpeg is already the best decoder available, and a
downscaled gray rawvideo stream piped into numpy is all the frame analysis here
needs, so the only dependencies are an ffmpeg build and numpy. The binaries
are found on PATH, at C:/ffmpeg/bin, or wherever FFMPEG_DIR points.

The interesting part is picking *which* few seconds of a clip to loop. See
choose_window().
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# --------------------------------------------------------------------------- #
# Encoder settings
# --------------------------------------------------------------------------- #

LOOP_SECONDS = 5.0        # target loop length
LOOP_MAX_WIDTH = 1280     # loops render at grid width; 1280 covers 2x on mobile
LOOP_FPS = 24
LOOP_CRF = 26             # visually clean for short loops at this size
PROXY_WIDTH = 160         # analysis resolution
PROXY_FPS = 8

# Window scoring weights. Tune these; they are the whole editorial policy.
W_MOTION = 0.45           # reward things actually happening
W_SHARPNESS = 0.30        # avoid the blurry, shaky parts
W_CLOSURE = 0.25          # reward first and last frame looking alike

# A cut is a spike relative to its neighbourhood. Comparing against the global
# maximum instead would read a fast pan -- high motion sustained over many
# frames -- as an unbroken run of cuts, rejecting exactly the windows worth
# looping.
CUT_RATIO = 4.0           # times the local median motion
CUT_FLOOR = 0.08          # below this, nothing counts as a cut at any ratio

# A window where nothing moves is a photo, and a photo is cheaper as a photo.
# Without this floor a static window collects full marks for sharpness and
# closure precisely because nothing happens, and wins.
MIN_MOTION = 0.08         # mean normalized motion required of a loop

# A clip only slightly longer than the window was almost certainly trimmed by
# hand to exactly the wanted moment. Searching inside it for a "better" window
# just shaves a second off a deliberate edit, so use the whole thing.
WHOLE_CLIP_TOLERANCE = 1.3   # times LOOP_SECONDS

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".3gp"}

# Where to look for the binaries. PATH first, then the usual places a Windows
# build gets unpacked to, so a plain unzip works without editing PATH. Override
# with the FFMPEG_DIR environment variable.
FFMPEG_SEARCH = [
    Path(os.environ["FFMPEG_DIR"]) if os.environ.get("FFMPEG_DIR") else None,
    Path("C:/ffmpeg/bin"),
    Path("C:/ffmpeg"),
    Path("C:/Program Files/ffmpeg/bin"),
]


def _tool(name: str) -> str:
    """Resolve ffmpeg/ffprobe, preferring PATH but falling back to known dirs."""
    found = shutil.which(name)
    if found:
        return found
    for base in FFMPEG_SEARCH:
        if base is None:
            continue
        for candidate in (base / f"{name}.exe", base / name):
            if candidate.is_file():
                return str(candidate)
    return name  # let the failure surface with a useful message


FFMPEG = _tool("ffmpeg")
FFPROBE = _tool("ffprobe")


def have_ffmpeg() -> bool:
    return Path(FFMPEG).is_file() or bool(shutil.which("ffmpeg"))


@dataclass
class Probe:
    duration: float
    width: int
    height: int
    fps: float
    creation_time: str | None


def probe(path: Path) -> Probe:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    meta = json.loads(out)
    stream = next(s for s in meta["streams"] if s["codec_type"] == "video")

    num, den = (stream.get("avg_frame_rate") or "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0

    w, h = int(stream["width"]), int(stream["height"])
    # Phone video stores portrait footage landscape plus a display matrix.
    # ffmpeg autorotates on decode, so report rotated dimensions to match what
    # the browser will actually lay out.
    rotation = 0
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"])) % 180
    if rotation == 90:
        w, h = h, w

    tags = {**meta.get("format", {}).get("tags", {}), **(stream.get("tags") or {})}
    return Probe(
        duration=float(meta["format"]["duration"]),
        width=w, height=h, fps=fps,
        creation_time=tags.get("creation_time"),
    )


def decode_proxy(path: Path, width: int = PROXY_WIDTH, fps: int = PROXY_FPS):
    """Decode the whole clip as small gray frames straight into a numpy array."""
    info = probe(path)
    height = int(round(info.height * width / info.width))
    height -= height % 2

    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path),
         "-vf", f"scale={width}:{height},fps={fps}",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout

    n = len(raw) // (width * height)
    if n == 0:
        raise RuntimeError(f"decoded no frames from {path}")
    return np.frombuffer(
        raw[: n * width * height], dtype=np.uint8
    ).reshape(n, height, width)


def frame_metrics(frames: np.ndarray):
    """Per-frame motion and sharpness over the proxy stack."""
    f = frames.astype(np.float32) / 255.0

    motion = np.zeros(len(f), dtype=np.float32)
    motion[1:] = np.abs(np.diff(f, axis=0)).mean(axis=(1, 2))

    # 4-neighbour Laplacian; its variance is the standard focus measure.
    lap = (4 * f[:, 1:-1, 1:-1]
           - f[:, :-2, 1:-1] - f[:, 2:, 1:-1]
           - f[:, 1:-1, :-2] - f[:, 1:-1, 2:])
    sharpness = lap.var(axis=(1, 2))

    return motion, sharpness


def _norm(x: np.ndarray) -> np.ndarray:
    """Robust 0..1 scaling over the 5th-95th percentile band."""
    lo, hi = np.percentile(x, 5), np.percentile(x, 95)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def detect_cuts(motion: np.ndarray, half_window: int = 8) -> np.ndarray:
    """Flag frames whose motion spikes far above the local median."""
    n = len(motion)
    if n < 2 * half_window + 1:
        return np.zeros(n, dtype=bool)
    padded = np.pad(motion, (half_window, half_window), mode="edge")
    local = np.array([
        np.median(padded[i:i + 2 * half_window + 1]) for i in range(n)
    ], dtype=np.float32)
    return (motion > np.maximum(local * CUT_RATIO, CUT_FLOOR)) & (motion > CUT_FLOOR)


def choose_window(frames: np.ndarray, seconds: float = LOOP_SECONDS,
                  proxy_fps: int = PROXY_FPS):
    """Pick the best `seconds`-long window to loop.

    Three signals, all computed on the proxy:

      motion     Reward something happening. A static window is a photo, and a
                 photo is cheaper served as a photo.
      sharpness  Penalise the blurry, shaky, mid-pan stretches.
      closure    Reward the first and last frame resembling each other, which is
                 what stops a loop from looking like a loop.

    Windows containing a scene cut are rejected outright: a loop that jumps
    between two shots reads as broken rather than as a loop.
    """
    win = max(2, int(round(seconds * proxy_fps)))
    if len(frames) <= win * WHOLE_CLIP_TOLERANCE:
        whole = len(frames) / proxy_fps
        note = ("clip shorter than window" if len(frames) <= win
                else "clip close to window length; treated as a deliberate trim")
        return 0.0, whole, {"note": note}

    motion, sharpness = frame_metrics(frames)
    m_norm, s_norm = _norm(motion), _norm(sharpness)

    f = frames.astype(np.float32) / 255.0
    cuts = detect_cuts(motion)

    def evaluate(start, require_motion):
        end = start + win
        if cuts[start + 1:end].any():
            return None
        mean_motion = float(m_norm[start:end].mean())
        if require_motion and mean_motion < MIN_MOTION:
            return None

        closure = 1.0 - float(np.abs(f[start] - f[end - 1]).mean()) * 4.0
        closure = max(0.0, min(1.0, closure))
        score = (W_MOTION * mean_motion
                 + W_SHARPNESS * float(s_norm[start:end].mean())
                 + W_CLOSURE * closure)
        return score, {
            "score": round(score, 4),
            "motion": round(mean_motion, 3),
            "sharpness": round(float(s_norm[start:end].mean()), 3),
            "closure": round(closure, 3),
        }

    # First pass demands real motion. If the whole clip is static, fall back to
    # ranking on sharpness and closure alone and say so.
    for require_motion in (True, False):
        best_score, best_start, best_diag = -1e9, None, {}
        for start in range(0, len(frames) - win):
            result = evaluate(start, require_motion)
            if result and result[0] > best_score:
                best_score, best_start, best_diag = result[0], start, result[1]
        if best_start is not None:
            if not require_motion:
                best_diag["note"] = "no window met the motion floor"
            return best_start / proxy_fps, seconds, best_diag

    return 0.0, seconds, {"note": "every window contained a scene cut"}


def encode_loop(src: Path, start: float, duration: float, out: Path,
                max_width: int = LOOP_MAX_WIDTH, crf: int = LOOP_CRF,
                fps: int = LOOP_FPS) -> None:
    """Encode the chosen window as a muted, web-safe H.264 loop.

    -an is not an optimisation. Browsers only autoplay muted video, so the audio
    track could never play and is pure weight. yuv420p plus profile high is what
    makes the result decodable everywhere, hardware decoders on phones included.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y",
         "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src),
         "-vf", f"scale='min({max_width},iw)':-2:flags=lanczos,fps={fps}",
         "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-crf", str(crf), "-preset", "slow",
         "-movflags", "+faststart", "-an", str(out)],
        check=True, capture_output=True,
    )


def extract_poster(src: Path, at: float, out: Path) -> None:
    """Pull one frame as PNG so the existing Pillow path can derive from it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(src),
         "-frames:v", "1", str(out)],
        check=True, capture_output=True,
    )
