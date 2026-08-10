"""OCR as graph nodes — a locate/extract path that reads pixels.

These are candidates on the same planes as everything else: `ocr_extract`
answers "take the data away" when the DOM cannot, and `ocr_verify` answers
"confirm it happened" when the only witness is what was drawn.

They are last-resort by design. Text recovered from pixels is worse than text
read from the DOM in every way that matters, so these carry a high cost in the
architecture and are chosen only when the cheaper paths cannot answer.
"""
from __future__ import annotations

from typing import ClassVar

from browsergraph.nodes.base import Node, register
from browsergraph.ports import Context


@register
class OcrExtract(Node):
    """Read the rendered page and put the text in the context.

    Screenshots first, then reads. The image is kept as an artifact, because a
    reading nobody can check against the picture it came from is not evidence.
    """

    kind: ClassVar[str] = "ocr_extract"

    def __init__(self, path: str = "", into: str = "ocr_text",
                 backend: str = "", name: str = ""):
        super().__init__(name)
        self.path = path
        self.into = into
        self.backend = backend

    @property
    def writes(self) -> tuple[str, ...]:      # type: ignore[override]
        return (self.into,)

    def run(self, ctx: Context) -> Context:
        import tempfile

        from browsergraph.ocr import read_text

        path = self.path or tempfile.mkstemp(prefix="bg-ocr-", suffix=".png")[1]
        saved = ctx.page.screenshot(path)
        ctx.artifacts.append(saved)

        reading = read_text(saved, backend=self.backend)
        ctx.data[self.into] = reading.text
        ctx.data[f"{self.into}_backend"] = reading.backend
        if reading.error:
            ctx.note(f"ocr: {reading.error}")
        else:
            ctx.note(f"ocr[{reading.backend}] read {reading.words} words "
                     f"in {reading.seconds:.1f}s")
        return ctx


@register
class OcrVerify(Node):
    """Confirm an expected string is *visible*, not merely present in the DOM.

    Different from every other verification here: a value can be in the DOM and
    invisible — hidden, covered, clipped, or drawn in a font that never loaded.
    This checks what a person would see.
    """

    kind: ClassVar[str] = "ocr_verify"
    verifies: bool = True
    writes: ClassVar[tuple[str, ...]] = ("visible", "ocr_text")

    def __init__(self, expected: str, path: str = "", backend: str = "",
                 name: str = ""):
        super().__init__(name)
        self.expected = expected
        self.path = path
        self.backend = backend

    def run(self, ctx: Context) -> Context:
        import tempfile

        from browsergraph.ocr import read_text

        path = self.path or tempfile.mkstemp(prefix="bg-ocrv-", suffix=".png")[1]
        saved = ctx.page.screenshot(path)
        ctx.artifacts.append(saved)

        reading = read_text(saved, backend=self.backend)
        ctx.data["ocr_text"] = reading.text
        seen = self.expected.lower() in reading.text.lower()
        ctx.data["visible"] = seen
        if not seen:
            ctx.fail(f"{self.expected!r} is not visible on the rendered page "
                     f"(ocr[{reading.backend or 'none'}] read {reading.words} words)"
                     + ("; no OCR backend is installed" if reading.error else ""))
        else:
            ctx.note(f"ocr confirmed {self.expected!r} is visible")
        return ctx
