"""Reading a page from its pixels, when the DOM will not do.

Most extraction should never come near OCR — the DOM is right there, and text
recovered from pixels is worse in every way that matters. This exists for the
cases where the DOM genuinely is not the answer:

* text baked into an image — a phone number on a banner, a price in a hero shot;
* a canvas or WebGL surface, which has no DOM to query at all;
* a page that renders text as vector paths;
* and the one that motivated this module: **checking that a screenshot contains
  text at all.**

That last use is not an afterthought. WebKit on a slim image rendered a page
with a perfect layout and not one glyph, and it did not error — the graph
reported success, the click landed, the element appeared, and the extracted
value was correct. Every data-level check passed. The only witness was the
picture, and nobody looks at pictures. `has_text` makes that a check.

Backends are interchangeable and ranked by what they cost, in the same spirit as
everything else here: the cheapest that can answer, and an honest report of
which ones are actually installed. No backend is required — `available()` is the
answer to "what can this machine do", not a promise.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from browsergraph.dimensions import LLMConfig


@runtime_checkable
class OcrBackend(Protocol):
    """Anything that can turn an image into text."""

    name: str
    cost: float          # relative expense, for choosing between them

    def available(self) -> bool:
        ...

    def read(self, image_path: str) -> str:
        ...


@dataclass
class Reading:
    """What an OCR pass produced, and which backend produced it."""
    text: str = ""
    backend: str = ""
    error: str = ""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error

    @property
    def words(self) -> int:
        return len(self.text.split())

    def to_dict(self) -> dict:
        return {"backend": self.backend, "words": self.words,
                "seconds": round(self.seconds, 2), "error": self.error,
                "text": self.text[:2000]}


# --- backends ---------------------------------------------------------------

@dataclass
class RapidOcr:
    """ONNX-based, pip-installable, no system packages.

    The best default on a machine you do not administer: `pip install
    rapidocr-onnxruntime` and nothing else, where tesseract needs a system
    binary and easyocr drags in torch.
    """
    name: str = "rapidocr"
    cost: float = 1.0

    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("rapidocr_onnxruntime") is not None

    def read(self, image_path: str) -> str:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore
        result, _ = RapidOCR()(image_path)
        if not result:
            return ""
        return "\n".join(line[1] for line in result if len(line) > 1)


@dataclass
class Tesseract:
    """The classic. Needs a system binary as well as the Python wrapper."""
    name: str = "tesseract"
    cost: float = 0.8

    def available(self) -> bool:
        import importlib.util
        return (importlib.util.find_spec("pytesseract") is not None
                and shutil.which("tesseract") is not None)

    def read(self, image_path: str) -> str:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
        return pytesseract.image_to_string(Image.open(image_path))


@dataclass
class EasyOcr:
    """Torch-based. Accurate, heavy, and slow to start."""
    name: str = "easyocr"
    cost: float = 3.0
    languages: tuple[str, ...] = ("en",)

    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("easyocr") is not None

    def read(self, image_path: str) -> str:
        import easyocr  # type: ignore
        reader = easyocr.Reader(list(self.languages), verbose=False)
        return "\n".join(reader.readtext(image_path, detail=0))


@dataclass
class PaddleOcr:
    name: str = "paddleocr"
    cost: float = 3.0

    def available(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("paddleocr") is not None

    def read(self, image_path: str) -> str:
        from paddleocr import PaddleOCR  # type: ignore
        out = PaddleOCR(use_angle_cls=True, lang="en", show_log=False).ocr(image_path)
        lines = []
        for page in out or []:
            for entry in page or []:
                if len(entry) > 1 and entry[1]:
                    lines.append(str(entry[1][0]))
        return "\n".join(lines)


@dataclass
class VisionModelOcr:
    """A multimodal model asked to transcribe — GLM, Kimi, Qwen-VL, any of them.

    The most capable and by far the most expensive, and the only one that can be
    asked a *question* about the image rather than for its text. It is last by
    cost, not by quality.

    It is also the one to distrust most: a model asked to read an image with no
    text in it will often produce plausible text anyway. That is exactly the
    failure `has_text` exists to detect, so this backend is deliberately not the
    default for that check.
    """
    name: str = "vision_model"
    cost: float = 8.0
    prompt: str = ("Transcribe every piece of text visible in this image, exactly. "
                   "If there is no text at all, reply with the single word NONE.")
    cfg: LLMConfig | None = None

    def available(self) -> bool:
        try:
            from browsergraph.dimensions import LLMConfig
            from browsergraph.models import Catalog
            cfg = self.cfg or LLMConfig.from_env()
            cat = Catalog.load(cfg.host, cfg.api_key)
            return bool(cat.reachable and cat.best("vision"))
        except Exception:
            return False

    def read(self, image_path: str) -> str:
        from browsergraph.dimensions import LLMConfig
        from browsergraph.vision import VisionClient
        cfg = self.cfg or LLMConfig.from_env()
        out = VisionClient(cfg).ask(self.prompt, image_path)
        return "" if out.strip().upper().startswith("NONE") else out


#: Cheapest first. `read_text` walks this order and uses the first that is
#: installed, so adding a backend changes what a machine can do without changing
#: any caller.
BACKENDS: list[OcrBackend] = [Tesseract(), RapidOcr(), EasyOcr(), PaddleOcr(),
                              VisionModelOcr()]


def available() -> list[str]:
    """Which OCR backends this machine can actually use."""
    return [b.name for b in BACKENDS if b.available()]


def report() -> str:
    lines = []
    for b in sorted(BACKENDS, key=lambda b: b.cost):
        ok = b.available()
        lines.append(f"  [{'ok  ' if ok else 'MISS'}] {b.name:<14} cost={b.cost:<5}"
                     + ("" if ok else f"  pip install {INSTALL.get(b.name, b.name)}"))
    return "\n".join(lines) or "  no OCR backends"


#: What to install for each, since the package name rarely matches the import.
INSTALL = {
    "tesseract": "pytesseract  (plus the tesseract-ocr system package)",
    "rapidocr": "rapidocr-onnxruntime",
    "easyocr": "easyocr",
    "paddleocr": "paddleocr paddlepaddle",
    "vision_model": "nothing — needs a reachable vision model (see LLMConfig)",
}


def read_text(image_path: str, backend: str = "") -> Reading:
    """Read an image with the cheapest available backend, or a named one.

    Returns a `Reading` rather than a bare string so a failure is data instead of
    an exception: OCR is a best-effort layer, and a caller usually wants to know
    that nothing could read the image, not to have their graph collapse.
    """
    import time

    chosen = [b for b in BACKENDS if b.name == backend] if backend else \
        sorted((b for b in BACKENDS), key=lambda b: b.cost)
    if backend and not chosen:
        return Reading(error=f"unknown OCR backend {backend!r}; "
                             f"known: {', '.join(b.name for b in BACKENDS)}")

    tried: list[str] = []
    for b in chosen:
        if not b.available():
            tried.append(b.name)
            continue
        started = time.monotonic()
        try:
            return Reading(text=b.read(image_path), backend=b.name,
                           seconds=time.monotonic() - started)
        except Exception as e:
            return Reading(backend=b.name, error=f"{type(e).__name__}: {e}",
                           seconds=time.monotonic() - started)
    return Reading(error=f"no OCR backend available (not installed: "
                         f"{', '.join(tried)}). Try: pip install rapidocr-onnxruntime")


#: Below this many distinct colours, an image is almost certainly text-free.
#: A rendered page with words in it has anti-aliased glyph edges, which produce
#: hundreds of intermediate shades; a page with none is large flat areas.
FLAT_COLOURS = 600


def has_text(image_path: str, *, use_ocr: bool = False) -> bool:
    """Does this screenshot contain any rendered text?

    The cheap check first, and it is the one to trust: count distinct colours.
    Anti-aliased glyphs produce hundreds of intermediate shades, and a page
    without them is large flat blocks. It needs no OCR backend and cannot
    hallucinate.

    `use_ocr` adds a confirming read when a backend is present. It is off by
    default because a model asked to read a blank image will often invent text,
    which would defeat the entire purpose of the check.

    This exists because WebKit on a slim image renders a perfectly laid-out page
    with no glyphs whatsoever, and nothing else in a run notices.
    """
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        # Without an image library the honest answer is "cannot tell", and the
        # safe direction for a check like this is not to raise a false alarm.
        return True

    try:
        with Image.open(image_path) as im:
            colours = im.convert("RGB").getcolors(maxcolors=1 << 24)
    except Exception:
        return True

    if colours is not None and len(colours) < FLAT_COLOURS:
        if use_ocr and available():
            return read_text(image_path).words > 0
        return False
    return True


@dataclass
class OcrResult:
    """A page read twice — from the DOM and from the pixels."""
    dom_text: str = ""
    ocr: Reading = field(default_factory=Reading)

    @property
    def only_in_image(self) -> list[str]:
        """Words OCR found that the DOM does not contain.

        The interesting output: text baked into an image, drawn on a canvas, or
        rendered as paths. Everything else is noise OCR added to what the DOM
        already told you more reliably.
        """
        dom = {w.lower().strip(".,:;()[]") for w in self.dom_text.split()}
        seen, out = set(), []
        for word in self.ocr.text.split():
            key = word.lower().strip(".,:;()[]")
            if len(key) > 2 and key not in dom and key not in seen:
                seen.add(key)
                out.append(word)
        return out
