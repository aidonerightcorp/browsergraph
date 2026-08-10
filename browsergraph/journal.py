"""Receipts that accumulate, in an order you can check.

`Run.receipt()` produces evidence for one run and hands it to you. What happened
next was up to you, and in practice what happened next was nothing: the receipt
went into a variable, the notebook closed, and the run left no trace.

A journal is the missing half. Append a receipt and it is on disk, in order,
with each line carrying the hash of the line before it. That chain is the whole
point:

* **Append-only in fact, not by convention.** A line edited after the fact
  breaks its successor's `previous` hash, and `verify()` names the first line
  where the chain stops adding up.
* **Order is evidence.** "This route scored well" means something different
  before and after the run that changed the code, and a bag of JSON files with
  timestamps does not reliably tell you which came first.
* **Cheap to read.** One JSON object per line. `grep` works. A journal that
  needs a database to inspect is a journal nobody inspects, which is the same
  argument `Evidence` makes for staying a plain dict.

What this is *not*: tamper-**proof**. Anyone who can write the file can rewrite
every line and recompute the whole chain. It is tamper-**evident** against edits
that do not bother, which is the realistic threat for a local experiment log,
and calling it more than that would be the kind of security claim this
repository is careful not to make.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

#: The first line has nothing before it, and needs something to point at.
GENESIS = "0" * 64


def _digest(payload: Mapping[str, Any], previous: str) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)
    return hashlib.sha256(f"{previous}\x00{blob}".encode()).hexdigest()


@dataclass(frozen=True)
class Entry:
    """One appended record, and where it sits in the chain."""
    index: int
    at: float
    previous: str
    digest: str
    kind: str
    body: Mapping[str, Any]

    def to_dict(self) -> dict:
        return {"index": self.index, "at": self.at, "previous": self.previous,
                "digest": self.digest, "kind": self.kind, "body": dict(self.body)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Entry:
        return cls(index=int(data.get("index", 0)), at=float(data.get("at", 0.0)),
                   previous=str(data.get("previous", GENESIS)),
                   digest=str(data.get("digest", "")),
                   kind=str(data.get("kind", "")),
                   body=dict(data.get("body") or {}))

    @property
    def recomputed(self) -> str:
        return _digest({"index": self.index, "at": self.at, "kind": self.kind,
                        "body": dict(self.body)}, self.previous)


class Journal:
    """An append-only log of receipts, chained so edits show up."""

    def __init__(self, path: str | pathlib.Path) -> None:
        self.path = pathlib.Path(path)

    # --- writing ------------------------------------------------------------

    def append(self, body: Mapping[str, Any], kind: str = "receipt",
               at: float | None = None) -> Entry:
        """Add one record. Returns what was written, chain and all.

        The write is a single `open(..., "a")` and one line, because a partial
        line is the one corruption this format cannot survive — the reader
        would take the truncated JSON as the end of the file and every later
        entry would vanish silently.
        """
        entries = list(self.read())
        previous = entries[-1].digest if entries else GENESIS
        index = len(entries)
        moment = time.time() if at is None else at
        payload = {"index": index, "at": moment, "kind": kind,
                   "body": dict(body)}
        entry = Entry(index=index, at=moment, previous=previous,
                      digest=_digest(payload, previous), kind=kind,
                      body=dict(body))

        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.to_dict(), sort_keys=True, default=str)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def record(self, receipt: Any, kind: str = "receipt") -> Entry:
        """Append a `TaskReceipt`, or anything with a `to_dict`."""
        body = receipt.to_dict() if hasattr(receipt, "to_dict") else dict(receipt)
        return self.append(body, kind=kind)

    # --- reading ------------------------------------------------------------

    def read(self) -> Iterator[Entry]:
        if not self.path.exists():
            return iter(())
        return self._read()

    def _read(self) -> Iterator[Entry]:
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield Entry.from_dict(json.loads(line))
                except json.JSONDecodeError:
                    # A half-written last line. Stop rather than guess: the
                    # alternative is silently treating a truncated record as
                    # the end of history.
                    return

    def __len__(self) -> int:
        return sum(1 for _ in self.read())

    def entries(self) -> list[Entry]:
        return list(self.read())

    def latest(self, kind: str = "") -> Entry | None:
        found = [e for e in self.read() if not kind or e.kind == kind]
        return found[-1] if found else None

    # --- checking -----------------------------------------------------------

    def verify(self) -> list[str]:
        """Every place the chain stops adding up, in order.

        Returns an empty list for an intact journal, including an empty one —
        a file that does not exist yet is not evidence of tampering.
        """
        problems: list[str] = []
        previous = GENESIS
        for position, entry in enumerate(self.read()):
            if entry.index != position:
                problems.append(f"line {position}: index says {entry.index}")
            if entry.previous != previous:
                problems.append(
                    f"line {position}: follows {entry.previous[:12]}… but the "
                    f"line before it hashes to {previous[:12]}…")
            if entry.digest != entry.recomputed:
                problems.append(
                    f"line {position}: contents do not match their own hash — "
                    f"this record was edited after it was written")
            previous = entry.digest
        return problems

    @property
    def intact(self) -> bool:
        return not self.verify()

    def text(self) -> str:
        entries = self.entries()
        if not entries:
            return f"{self.path}: empty"
        problems = self.verify()
        lines = [f"{self.path}: {len(entries)} entries, "
                 + ("chain intact" if not problems
                    else f"{len(problems)} problem(s)")]
        for entry in entries[-5:]:
            ok = entry.body.get("ok")
            mark = "" if ok is None else ("  ok" if ok else "  FAILED")
            lines.append(f"  {entry.index:>4}  {entry.kind:<10} "
                         f"{entry.digest[:12]}…{mark}")
        lines += [f"  ! {p}" for p in problems[:5]]
        return "\n".join(lines)


def replay(path: str | pathlib.Path, evidence=None, context: str = "global"):
    """Fold a whole journal back into an `Evidence` store.

    This is what makes the journal worth keeping rather than merely writing: a
    machine that loses its evidence file can rebuild it from the receipts, and
    two machines that ran different things can pool what they learned by
    concatenating journals.
    """
    from browsergraph.evidence import Evidence
    from browsergraph.receipt import TaskReceipt

    store = evidence if evidence is not None else Evidence()
    for entry in Journal(path).read():
        if entry.kind != "receipt":
            continue
        try:
            store.from_receipt(TaskReceipt.from_dict(entry.body), context=context)
        except Exception:                          # noqa: BLE001 - skip and go on
            continue
    return store
