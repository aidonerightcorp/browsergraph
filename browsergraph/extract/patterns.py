"""Deterministic extractors for public contact data.

No model involved: these run on every page, so they must be cheap, stable and
conservative. **False positives are worse than misses here** — a phone number
that is actually a product SKU pollutes a dataset silently, whereas a missed
number is visible as an empty field. Every pattern below is therefore anchored
and validated rather than greedy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# --- email ------------------------------------------------------------------

_EMAIL = re.compile(
    r"(?<![\w.+-])([A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24})(?![\w-])")

# Addresses that are almost never a real contact.
_EMAIL_NOISE = re.compile(
    r"^(?:no-?reply|do-?not-?reply|postmaster|mailer-daemon|abuse|"
    r"example|test|user|name|email|your)@|@(?:example|test|localhost|sentry|"
    r"\d+\.\d+)\b", re.I)

_IMG_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)


def emails(text: str) -> list[str]:
    """Unique, lower-cased, plausible addresses in document order."""
    out, seen = [], set()
    for m in _EMAIL.finditer(text or ""):
        value = m.group(1).strip(".").lower()
        if _EMAIL_NOISE.search(value) or _IMG_EXT.search(value):
            continue
        if value.count("@") != 1 or ".." in value:
            continue
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


# --- phone ------------------------------------------------------------------

_PHONE = re.compile(r"""
    (?<![\w])
    (?:(\+\d{1,3})[\s.\-]?)?          # country code
    (?:\((\d{2,4})\)|(\d{2,4}))       # area code, bracketed or not
    [\s.\-]?
    (\d{3,4})[\s.\-]?(\d{3,4})        # subscriber
    (?:\s*(?:x|ext\.?|extension)\s*(\d{1,6}))?
    (?![\w])
""", re.X)

# Sequences that look like phones but are not.
_PHONE_REJECT = (
    re.compile(r"^(19|20)\d{2}[\s.\-]?(0[1-9]|1[0-2])"),   # dates
    re.compile(r"^0+$"),
)


@dataclass
class Phone:
    raw: str
    digits: str
    country: str = ""
    ext: str = ""

    @property
    def e164ish(self) -> str:
        return f"{self.country}{self.digits}" if self.country else self.digits


#: A bare number flanked by more bare numbers is part of a sequence, not a phone.
#: Found on python.org, whose homepage prints a Fibonacci series: the run
#: "... 233 377 610 987" yielded the "phone number" 377 610 987. Any page with a
#: numeric table, a code sample or a list of statistics does the same thing.
_ADJACENT_DIGITS = re.compile(r"\d[\s]*$")
_LEADING_DIGITS = re.compile(r"^[\s]*\d")


def phones(text: str, min_digits: int = 9, max_digits: int = 15) -> list[Phone]:
    """Plausible phone numbers. Length-validated to exclude IDs and years.

    Deliberately conservative, in the same direction as the rest of this module:
    a false positive silently poisons a dataset, while a miss is a visible empty
    field that someone notices.
    """
    text = text or ""
    out, seen = [], set()
    for m in _PHONE.finditer(text):
        raw = m.group(0).strip()
        country = (m.group(1) or "").strip()
        digits = re.sub(r"\D", "", raw[len(country):] if country else raw)
        if m.group(6):
            digits = digits[: -len(m.group(6))] or digits
        if not (min_digits <= len(digits) <= max_digits):
            continue
        if any(p.match(re.sub(r"\D", "", raw)) for p in _PHONE_REJECT):
            continue
        if len(set(digits)) <= 2:                     # 000000000, 111111111
            continue

        # Without a country code, nine bare digits is not a dialable number
        # anywhere this library is likely to be pointed, and it is the single
        # most common shape of numeric noise. Ten is the floor.
        if not country and len(digits) < 10:
            continue

        # Part of a longer run of numbers -> a sequence, table or code output.
        if not country and re.fullmatch(r"[\d\s]+", raw):
            if _ADJACENT_DIGITS.search(text[max(0, m.start() - 12):m.start()]) or \
                    _LEADING_DIGITS.match(text[m.end():m.end() + 12]):
                continue
        key = country + digits
        if key not in seen:
            seen.add(key)
            out.append(Phone(raw=raw, digits=digits, country=country,
                             ext=m.group(6) or ""))
    return out


# --- address ----------------------------------------------------------------

_STREET_WORD = (r"street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|"
                r"way|court|ct|place|pl|parkway|pkwy|highway|hwy|suite|ste|unit|"
                r"floor|fl|terrace|ter|circle|cir|square|sq")

_US_ADDRESS = re.compile(
    rf"""\b(\d{{1,6}}\s+[A-Za-z0-9.'\-]+(?:\s+[A-Za-z0-9.'\-]+){{0,4}}\s+
        (?:{_STREET_WORD})\b\.?
        (?:\s*(?:suite|ste|unit|apt|\#)\s*[\w-]+)?
        (?:\s*,\s*[A-Za-z .'\-]{{2,30}})?          # city
        (?:\s*,?\s*([A-Z]{{2}}))?                  # state
        (?:\s*,?\s*(\d{{5}}(?:-\d{{4}})?))?        # zip
    )""", re.X | re.I)

_POSTCODE_UK = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b")


@dataclass
class Address:
    raw: str
    state: str = ""
    postcode: str = ""
    country_hint: str = ""


def addresses(text: str) -> list[Address]:
    """Street addresses. US-style plus UK postcodes; conservative by design."""
    out, seen = [], set()
    flat = re.sub(r"\s+", " ", text or "")
    for m in _US_ADDRESS.finditer(flat):
        raw = m.group(1).strip(" ,")
        if len(raw) < 8 or len(raw) > 200:
            continue
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            out.append(Address(raw=raw, state=(m.group(2) or ""),
                               postcode=(m.group(3) or ""),
                               country_hint="US" if m.group(3) else ""))
    for m in _POSTCODE_UK.finditer(flat):
        pc = m.group(1)
        start = max(0, m.start() - 90)
        raw = flat[start:m.end()].split(",")[-3:]
        joined = ", ".join(s.strip() for s in raw if s.strip())
        key = joined.lower()
        if pc.upper() in {a.postcode.upper() for a in out} or key in seen:
            continue
        if re.search(r"\d", joined):
            seen.add(key)
            out.append(Address(raw=joined, postcode=pc, country_hint="UK"))
    return out


# --- social / identifiers ---------------------------------------------------

_SOCIAL_HOSTS = {
    "linkedin.com": "linkedin", "twitter.com": "twitter", "x.com": "twitter",
    "facebook.com": "facebook", "instagram.com": "instagram",
    "youtube.com": "youtube", "tiktok.com": "tiktok", "pinterest.com": "pinterest",
    "github.com": "github", "crunchbase.com": "crunchbase",
}


def socials(urls: list[str]) -> dict[str, str]:
    """First profile URL seen per platform."""
    found: dict[str, str] = {}
    for url in urls or []:
        low = url.lower()
        for host, name in _SOCIAL_HOSTS.items():
            if host in low and name not in found:
                tail = low.split(host, 1)[1].strip("/")
                if tail and not tail.startswith(("share", "sharer", "intent")):
                    found[name] = url
    return found


_VAT = re.compile(r"\b(?:VAT|GST|ABN|EIN|TIN)[\s.:#]*([A-Z]{0,2}[\d\s-]{7,15})\b", re.I)


def tax_ids(text: str) -> list[str]:
    return list(dict.fromkeys(
        re.sub(r"\s+", "", m.group(1)) for m in _VAT.finditer(text or "")))


# --- aggregate --------------------------------------------------------------

@dataclass
class Contacts:
    emails: list[str] = field(default_factory=list)
    phones: list[Phone] = field(default_factory=list)
    addresses: list[Address] = field(default_factory=list)
    socials: dict[str, str] = field(default_factory=dict)
    tax_ids: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.emails or self.phones or self.addresses or self.socials)

    def to_dict(self) -> dict:
        return {
            "emails": self.emails,
            "phones": [{"raw": p.raw, "digits": p.digits, "country": p.country,
                        "ext": p.ext} for p in self.phones],
            "addresses": [{"raw": a.raw, "state": a.state, "postcode": a.postcode,
                           "country_hint": a.country_hint} for a in self.addresses],
            "socials": self.socials,
            "tax_ids": self.tax_ids,
        }

    def merge(self, other: Contacts) -> Contacts:
        def uniq(a, b, key=lambda x: x):
            seen, out = set(), []
            for item in list(a) + list(b):
                k = key(item)
                if k not in seen:
                    seen.add(k)
                    out.append(item)
            return out
        return Contacts(
            emails=uniq(self.emails, other.emails),
            phones=uniq(self.phones, other.phones, key=lambda p: p.e164ish),
            addresses=uniq(self.addresses, other.addresses, key=lambda a: a.raw.lower()),
            socials={**other.socials, **self.socials},
            tax_ids=uniq(self.tax_ids, other.tax_ids),
        )


def extract_contacts(text: str, urls: list[str] | None = None,
                     extra_emails: list[str] | None = None) -> Contacts:
    found = emails(text)
    for addr in (extra_emails or []):
        cleaned = emails(addr)
        for a in cleaned:
            if a not in found:
                found.append(a)
    return Contacts(emails=found, phones=phones(text),
                    addresses=addresses(text), socials=socials(urls or []),
                    tax_ids=tax_ids(text))
