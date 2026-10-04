"""How well the eyes read small text, per way of sending the screen.

    uv run python scripts/eyes_eval.py [--runs 3] [--models sonnet-5-5,haiku-4-5]

Paid: about 40 vision calls (~$0.50). Renders `eyes_eval/flights.html` with
headless Chrome as a 1920x1080 screen at 1x and 2x (Retina), before (`?a`) and
after (`?b`) one itinerary is opened, then reads the "after" frame through the
real `AnthropicVision` — same prompt, same code path — once per way of
encoding it, with the "before" frame's reading as `previous`. Each reading is
scored against the values the opened itinerary shows: found exactly, or read
wrong (16 Oct as 18 or 10, and the like). Writes the table to
`docs/eyes-eval.md`.

The crop is the page's rule (`web/eyes.js`, `cropRect`) mirrored here, so
what is measured is what the page sends.
"""

import argparse
import asyncio
import io
import json
import re
import statistics
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

from voice_agent.llm.vision import BY_NAME, AnthropicVision, Seen

HERE = Path(__file__).parent / "eyes_eval"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# The page's constants (web/eyes.js).
THUMB_W, THUMB_H, LEVEL = 256, 144, 16
CROP_PAD, CROP_MIN_W, CROP_MIN_H, CROP_MAX_SHARE, CROP_MAX_EDGE = 4, 64, 32, 0.4, 1568

TRUTH: dict[str, str] = {
    "date 16 Oct": r"\b16 Oct",
    "return 19 Oct": r"\b19 Oct",
    "depart 06:10": r"06:10",
    "Riga 07:05": r"07:05",
    "Riga out 07:55": r"07:55",
    "lands 10:45": r"10:45",
    "transfer 50 min": r"\b50 ?min",
    "flights BT 315/211": r"BT ?315",
    "total €1,186": r"1[,.]?186\b",
    "bag €38": r"€ ?38\b",
}
"""The values in the opened itinerary, as a pattern each."""

MISREAD = re.compile(
    r"\b(1[0358]|6) Oct|\b1[,.]?1(?!86)\d\d\b|\bBT ?(?!315|211)\d{3}\b|\b(30|58|60|5) ?min"
)
"""What the values look like read wrong: 16 Oct as 10, 13, 15, 18 or 6; €1,186
as 1,136 or 1,188; another flight number; the 50 min transfer as 30 or 58.
The page shows 14 and 19 Oct, and 55 min, so those are not misreads."""


@dataclass(frozen=True)
class Variant:
    name: str
    width: int
    fmt: str  # "jpeg" or "png"
    quality: int = 85
    crop: bool = False


VARIANTS = (
    Variant("1024 · JPEG 0.6 (before)", 1024, "jpeg", 60),
    Variant("1024 · JPEG 0.85", 1024, "jpeg", 85),
    Variant("1280 · JPEG 0.85", 1280, "jpeg", 85),
    Variant("1280 · PNG", 1280, "png"),
    Variant("1024 · JPEG 0.85 + crop", 1024, "jpeg", 85, crop=True),
    Variant("1280 · JPEG 0.85 + crop", 1280, "jpeg", 85, crop=True),
)


def render(state: str, scale: int) -> Image.Image:
    out = HERE / f"{state}-{scale}.png"
    if not out.exists():
        subprocess.run(
            [
                CHROME,
                "--headless",
                "--hide-scrollbars",
                "--disable-gpu",
                "--window-size=1920,1080",
                f"--force-device-scale-factor={scale}",
                f"--screenshot={out}",
                f"file://{HERE / 'flights.html'}?{state}",
            ],
            check=True,
            capture_output=True,
        )
    return Image.open(out).convert("RGB")


def thumb(image: Image.Image) -> list[int]:
    # PIL's "L" is Rec. 601 luma, like the page's `grey`.
    small = image.resize((THUMB_W, THUMB_H), Image.Resampling.BOX).convert("L")
    return list(small.tobytes())


def changed_rect(before: list[int], after: list[int]) -> tuple[int, int, int, int] | None:
    cells = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if abs(a - b) > LEVEL]
    if not cells:
        return None
    xs, ys = [i % THUMB_W for i in cells], [i // THUMB_W for i in cells]
    return min(xs), min(ys), max(xs) - min(xs) + 1, max(ys) - min(ys) + 1


def grow(lo: int, hi: int, least: int, limit: int) -> tuple[int, int]:
    if hi - lo < least:
        lo = max(0, lo - (least - (hi - lo)) // 2)
        hi = min(limit, lo + least)
        lo = max(0, hi - least)
    return lo, hi


def crop_rect(
    rect: tuple[int, int, int, int], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """The page's rule: pad, grow to a minimum, none past 40% of the screen;
    in the screen's own pixels."""
    x, y, w, h = rect
    x0, x1 = grow(max(0, x - CROP_PAD), min(THUMB_W, x + w + CROP_PAD), CROP_MIN_W, THUMB_W)
    y0, y1 = grow(max(0, y - CROP_PAD), min(THUMB_H, y + h + CROP_PAD), CROP_MIN_H, THUMB_H)
    if (x1 - x0) * (y1 - y0) > CROP_MAX_SHARE * THUMB_W * THUMB_H:
        return None
    sx, sy = width / THUMB_W, height / THUMB_H
    return round(x0 * sx), round(y0 * sy), round(x1 * sx), round(y1 * sy)


def encode(image: Image.Image, fmt: str, quality: int) -> bytes:
    out = io.BytesIO()
    if fmt == "png":
        image.save(out, "PNG")
    else:
        image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def frames(variant: Variant, before: Image.Image, after: Image.Image) -> tuple[bytes, bytes | None]:
    scale = min(1, variant.width / after.width)
    size = (round(after.width * scale), round(after.height * scale))
    full = encode(after.resize(size, Image.Resampling.LANCZOS), variant.fmt, variant.quality)
    if not variant.crop:
        return full, None
    rect = changed_rect(thumb(before), thumb(after))
    box = crop_rect(rect, after.width, after.height) if rect else None
    if box is None:
        return full, None
    piece = after.crop(box)
    edge = max(piece.size)
    if edge > CROP_MAX_EDGE:
        k = CROP_MAX_EDGE / edge
        piece = piece.resize(
            (round(piece.width * k), round(piece.height * k)), Image.Resampling.LANCZOS
        )
    return full, encode(piece, "png", 0)


def score(seen: Seen) -> tuple[int, list[str]]:
    text = " ".join([seen.screen, *seen.events])
    found = sum(1 for pattern in TRUTH.values() if re.search(pattern, text))
    return found, [m.group(0) for m in MISREAD.finditer(text)]


@dataclass
class Row:
    variant: str
    model: str
    scale: int
    found: list[int]
    misreads: list[str]
    ms: list[int]
    tokens_in: list[int]
    tokens_out: list[int]
    kb: float
    crop_px: str


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--models", default="sonnet-5-5,haiku-4-5")
    parser.add_argument("--scales", default="1,2")
    parser.add_argument("--raw", help="where to write every reading, as JSON")
    args = parser.parse_args()
    gate = asyncio.Semaphore(4)
    models = args.models.split(",")
    scales = [int(s) for s in args.scales.split(",")]

    reference = AnthropicVision(BY_NAME["sonnet-5-5"])
    before_1 = render("a", 1)
    previous = (await reference.look(frames(VARIANTS[2], before_1, before_1)[0], "")).screen
    print(f"previous screen: {previous}\n", file=sys.stderr)

    async def one(vision: AnthropicVision, jpeg: bytes, crop: bytes | None) -> Seen:
        async with gate:
            return await vision.look(jpeg, previous, crop)

    rows: list[Row] = []
    jobs = []
    for scale in scales:
        before, after = render("a", scale), render("b", scale)
        for variant in VARIANTS:
            full, crop = frames(variant, before, after)
            for model in models:
                runs = args.runs if model == models[0] else 1
                vision = AnthropicVision(BY_NAME[model])
                crop_px = "-"
                if crop:
                    crop_px = "x".join(map(str, Image.open(io.BytesIO(crop)).size))
                row = Row(
                    variant.name,
                    model,
                    scale,
                    [],
                    [],
                    [],
                    [],
                    [],
                    (len(full) + len(crop or b"")) / 1024,
                    crop_px,
                )
                rows.append(row)
                jobs += [(row, one(vision, full, crop)) for _ in range(runs)]

    raw: list[dict[str, object]] = []
    results = await asyncio.gather(*(job for _, job in jobs), return_exceptions=True)
    for (row, _), seen in zip(jobs, results, strict=True):
        if isinstance(seen, BaseException):
            print(f"{row.variant} {row.model}: {seen}", file=sys.stderr)
            continue
        raw.append(
            {
                "variant": row.variant,
                "model": row.model,
                "scale": row.scale,
                "screen": seen.screen,
                "events": list(seen.events),
            }
        )
        found, wrong = score(seen)
        row.found.append(found)
        row.misreads += wrong
        row.ms.append(seen.ms)
        row.tokens_in.append(seen.input_tokens)
        row.tokens_out.append(seen.output_tokens)

    lines = [
        "| Screen | Sent | Model | Values found | Misreads | Reading ms | Tokens in / out "
        "| KB | Crop px |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if not row.found:
            continue
        lines.append(
            f"| {1920 * row.scale}x{1080 * row.scale} | {row.variant} | {row.model} "
            f"| {statistics.mean(row.found):.1f} / {len(TRUTH)} "
            f"| {len(row.misreads)}{' (' + ', '.join(row.misreads) + ')' if row.misreads else ''} "
            f"| {statistics.median(row.ms):.0f} "
            f"| {statistics.mean(row.tokens_in):.0f} / {statistics.mean(row.tokens_out):.0f} "
            f"| {row.kb:.0f} | {row.crop_px} |"
        )
    table = "\n".join(lines)
    if args.raw:
        write(Path(args.raw), json.dumps(raw, indent=1, ensure_ascii=False))
    print(table)
    write(
        Path(__file__).parent.parent / "docs" / "eyes-eval.md",
        "# The eyes' eval\n\n"
        "`uv run python scripts/eyes_eval.py`: a flight-results page "
        "(`scripts/eyes_eval/flights.html`) with one itinerary opened, read through the real "
        f"vision call. {len(TRUTH)} values to read; values found are the mean over runs, "
        f"misreads the total over runs ({args.runs} runs for {models[0]}, 1 for the others).\n\n"
        f"{table}\n",
    )


if __name__ == "__main__":
    asyncio.run(main())
