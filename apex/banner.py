"""APEX startup banner — toxic-green monster + hot-pink graffiti wordmark.

Pure stdlib, zero deps (matches APEX's offline ethos). The banner only paints
colour when writing to a real TTY that isn't opted out via NO_COLOR, and it is
never emitted on the machine-readable paths (e.g. `elevate --stdout`), so piped
output stays clean.

Aesthetic: msfconsole meets Metalocalypse — a snarling radioactive beast in
front of an APEX graffiti tag. Lime green (#39FF14) + hot pink (#FF2D95).
"""

from __future__ import annotations

import os
import sys

# ─── palette ────────────────────────────────────────────────────────────────
# truecolor (24-bit) values; we fall back to the nearest bright 16-colour code
# when the terminal doesn't advertise truecolor.
_TOXIC = (57, 255, 20)     # #39FF14  radioactive lime — the monster
_LIME = (140, 255, 60)     # brighter accent / graffiti drips
_PINK = (255, 45, 149)     # #FF2D95  hot pink — the wordmark
_MAGENTA = (255, 0, 127)   # deep magenta — spray drips
_DIM = (60, 120, 40)       # shadow green

_FALLBACK = {              # (r,g,b) -> SGR code for non-truecolor terminals
    _TOXIC: "92",   # bright green
    _LIME: "92",
    _PINK: "95",    # bright magenta
    _MAGENTA: "35",
    _DIM: "32",
}

# ─── the art ─────────────────────────────────────────────────────────────────
# Monster (front) — a snarling toxic beast head.
_MONSTER = r"""
          /\_       _/\
         /  \\_   _//  \
        | () \ \_/ / () |
         \  /_/   \_\  /
          \/  .---.  \/
       __/`  (  o  )  `\__
      /   \   `._.'   /   \
      |    \  /VvVv\  /    |
       \    `/ |||| \'    /
        `.__/  |||| _\__.'
             `-'  `-'
"""

# Wordmark (the graffiti tag behind the beast) — chunky block letters.
_WORDMARK = r"""
 █████    ██████   ███████  ██   ██
██   ██   ██   ██  ██        ██ ██
███████   ██████   █████      ███
██   ██   ██       ██        ██ ██
██   ██   ██       ███████  ██   ██"""

# spray-paint drips hanging off the tag
_DRIPS = r"""
 ╻    ╻   ╻    ╻      ╻   ╻    ╻
 ╹    ┃   ╹    ┃      ╹   ┃    ╹
      ╹        ╹          ╹"""

_TAGLINE = "elevate any script to its apex standard"


# ─── colour plumbing ─────────────────────────────────────────────────────────
def _truecolor() -> bool:
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _paint(s: str, rgb, *, on: bool) -> str:
    if not on:
        return s
    if _truecolor():
        r, g, b = rgb
        return "\033[38;2;{0};{1};{2}m{3}\033[0m".format(r, g, b, s)
    return "\033[{0}m{1}\033[0m".format(_FALLBACK.get(rgb, "0"), s)


def supports_banner(stream=None) -> bool:
    """True when it's safe/appropriate to paint a banner to `stream`."""
    stream = stream or sys.stdout
    try:
        tty = stream.isatty()
    except Exception:
        tty = False
    return tty and os.environ.get("NO_COLOR") is None


def render(color: bool = True) -> str:
    """Return the full banner as a string (caller decides where it goes)."""
    lines = []
    # graffiti wordmark first (the wall tag "behind" the beast)
    for ln in _WORDMARK.strip("\n").splitlines():
        lines.append(_paint(ln, _PINK, on=color))
    for ln in _DRIPS.strip("\n").splitlines():
        lines.append(_paint(ln, _MAGENTA, on=color))
    lines.append("")
    # the monster in toxic green
    for ln in _MONSTER.strip("\n").splitlines():
        lines.append("    " + _paint(ln, _TOXIC, on=color))
    lines.append("")
    tag = _paint("«", _MAGENTA, on=color) + _paint(" " + _TAGLINE + " ", _LIME, on=color) + _paint("»", _MAGENTA, on=color)
    lines.append("        " + tag)
    return "\n".join(lines)


def emit(stream=None, *, force: bool = False) -> None:
    """Print the banner to `stream` when appropriate (no-op if not a TTY)."""
    stream = stream or sys.stdout
    if not force and not supports_banner(stream):
        return
    stream.write(render(color=force or supports_banner(stream)) + "\n\n")


if __name__ == "__main__":  # `python3 -m apex.banner` to preview
    sys.stdout.write(render(color=True) + "\n")
