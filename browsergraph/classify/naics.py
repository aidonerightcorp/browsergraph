"""NAICS classification from website content.

Deterministic keyword scoring over the 20 NAICS sectors, plus a set of common
subsectors. A model can refine the result, but the deterministic pass runs
first and always: it is reproducible, free, and explains itself.

**Confidence is reported honestly.** A site whose text matches three sectors
weakly is `low` confidence, and callers should treat that as "unclassified"
rather than as a code. Silently emitting a plausible-looking NAICS code for
every input is the failure mode this module is written to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: NAICS 2-digit sectors.
SECTORS: dict[str, str] = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31-33": "Manufacturing",
    "42": "Wholesale Trade",
    "44-45": "Retail Trade",
    "48-49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services (except Public Administration)",
    "92": "Public Administration",
}

#: Weighted keywords per sector. Weight 3 = near-decisive, 1 = weak signal.
KEYWORDS: dict[str, list[tuple[str, int]]] = {
    "11": [("farm", 3), ("agricultur", 3), ("crop", 2), ("livestock", 3),
           ("fishery", 3), ("forestry", 3), ("harvest", 1), ("orchard", 2)],
    "21": [("mining", 3), ("quarry", 3), ("drilling", 2), ("oil and gas", 3),
           ("petroleum extraction", 3), ("coal", 2), ("ore", 1)],
    "22": [("utility", 2), ("electric power", 3), ("water treatment", 3),
           ("natural gas distribution", 3), ("sewage", 2), ("grid operator", 2)],
    "23": [("construction", 3), ("contractor", 2), ("roofing", 3), ("plumbing", 3),
           ("hvac", 3), ("remodel", 2), ("excavation", 2), ("bricklay", 2),
           ("general contractor", 3), ("renovation", 2)],
    "31-33": [("manufactur", 3), ("factory", 2), ("fabrication", 2), ("assembly line", 3),
              ("production facility", 2), ("machining", 3), ("foundry", 3),
              ("oem", 1), ("industrial equipment", 2)],
    "42": [("wholesale", 3), ("distributor", 3), ("bulk supply", 2),
           ("trade supplier", 2), ("reseller", 1)],
    "44-45": [("shop now", 2), ("add to cart", 3), ("free shipping", 2), ("retail", 2),
              ("our store", 2), ("checkout", 2), ("boutique", 2), ("storefront", 2)],
    "48-49": [("logistics", 3), ("freight", 3), ("shipping company", 3), ("trucking", 3),
              ("warehousing", 3), ("courier", 3), ("last mile", 2), ("fleet", 1)],
    "51": [("software", 2), ("saas", 3), ("publishing", 2), ("streaming", 2),
           ("telecommunication", 3), ("data center", 2), ("media production", 2),
           ("broadcast", 2), ("app development", 2)],
    "52": [("bank", 3), ("insurance", 3), ("lending", 3), ("mortgage", 3),
           ("investment", 2), ("credit union", 3), ("underwriting", 3),
           ("wealth management", 3), ("brokerage", 3)],
    "53": [("real estate", 3), ("property management", 3), ("apartments for rent", 3),
           ("leasing", 2), ("realtor", 3), ("commercial property", 3), ("landlord", 2)],
    "54": [("consulting", 3), ("law firm", 3), ("attorney", 3), ("accounting", 3),
           ("architect", 3), ("engineering services", 3), ("marketing agency", 3),
           ("advisory", 2), ("cpa", 3), ("design studio", 2)],
    "55": [("holding company", 3), ("corporate headquarters", 2),
           ("portfolio companies", 2)],
    "56": [("staffing", 3), ("recruitment", 3), ("employment agency", 3),
           ("facilities management", 3), ("call center", 3), ("janitorial", 3),
           ("waste management", 3), ("security services", 2), ("bpo", 2),
           ("outsourcing", 2)],
    "61": [("school", 3), ("university", 3), ("college", 3), ("training courses", 3),
           ("curriculum", 2), ("tutoring", 3), ("academy", 2), ("enrollment", 2),
           ("students", 1)],
    "62": [("clinic", 3), ("hospital", 3), ("dental", 3), ("physician", 3),
           ("healthcare", 2), ("patients", 2), ("therapy", 2), ("nursing", 3),
           ("medical center", 3), ("pharmacy", 3)],
    "71": [("museum", 3), ("gallery", 2), ("theatre", 3), ("theater", 3),
           ("fitness center", 3), ("golf club", 3), ("casino", 3), ("amusement", 3),
           ("sports team", 2)],
    "72": [("restaurant", 3), ("hotel", 3), ("cafe", 3), ("catering", 3),
           ("menu", 2), ("reservations", 2), ("bed and breakfast", 3), ("bar &", 2),
           ("dining", 2)],
    "81": [("repair services", 3), ("salon", 3), ("barber", 3), ("dry cleaning", 3),
           ("auto repair", 3), ("funeral", 3), ("pet grooming", 3), ("laundry", 2)],
    "92": [("city of", 2), ("county government", 3), ("municipal", 3),
           ("public agency", 3), ("department of", 2), (".gov", 2)],
}


@dataclass
class Classification:
    code: str = ""
    sector: str = ""
    confidence: str = "none"          # high | medium | low | none
    score: float = 0.0
    margin: float = 0.0               # lead over the runner-up
    evidence: list[str] = field(default_factory=list)
    runners_up: list[tuple[str, float]] = field(default_factory=list)
    method: str = "keyword"

    @property
    def usable(self) -> bool:
        """True only at medium or better — low confidence means unclassified."""
        return self.confidence in ("high", "medium")

    def to_dict(self) -> dict:
        return {"code": self.code, "sector": self.sector,
                "confidence": self.confidence, "score": round(self.score, 2),
                "margin": round(self.margin, 2), "evidence": self.evidence[:8],
                "runners_up": [(c, round(s, 2)) for c, s in self.runners_up[:3]],
                "method": self.method, "usable": self.usable}


def score_sectors(text: str) -> dict[str, tuple[float, list[str]]]:
    """Per-sector score and the terms that produced it."""
    low = (text or "").lower()
    if not low:
        return {}
    out: dict[str, tuple[float, list[str]]] = {}
    for code, terms in KEYWORDS.items():
        total, hits = 0.0, []
        for term, weight in terms:
            n = low.count(term)
            if n:
                # Diminishing returns: 50 mentions of "software" is not 50x
                # the evidence of one, and unweighted counts let a navigation
                # menu dominate the whole classification.
                total += weight * (1 + min(n - 1, 4) * 0.25)
                hits.append(term)
        if total:
            out[code] = (total, hits)
    return out


def classify(text: str, title: str = "", url: str = "") -> Classification:
    """Classify from page content. Title and URL are weighted more heavily."""
    weighted = " ".join([(title + " ") * 3, (url + " ") * 2, text or ""])
    scores = score_sectors(weighted)
    if not scores:
        return Classification(confidence="none", method="keyword")

    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    top_code, (top_score, hits) = ranked[0]
    second = ranked[1][1][0] if len(ranked) > 1 else 0.0
    margin = top_score - second

    if top_score >= 6 and margin >= 3:
        confidence = "high"
    elif top_score >= 3 and margin >= 1.5:
        confidence = "medium"
    elif top_score > 0:
        confidence = "low"
    else:
        confidence = "none"

    return Classification(
        code=top_code, sector=SECTORS[top_code], confidence=confidence,
        score=top_score, margin=margin, evidence=hits,
        runners_up=[(c, s) for c, (s, _) in ranked[1:4]],
    )


def refine_with_llm(base: Classification, text: str, client, title: str = "") -> Classification:
    """Optional second pass. Only consulted when the deterministic result is weak.

    The model may only choose among the top candidates — it cannot invent a
    code, which keeps output inside the NAICS vocabulary.
    """
    if base.usable or client is None:
        return base
    options = [base.code] + [c for c, _ in base.runners_up]
    options = [c for c in options if c] or list(SECTORS)
    listing = "\n".join(f"{c}: {SECTORS[c]}" for c in options)
    prompt = (f"Choose the single best NAICS sector for this business.\n"
              f"Reply with the code only.\n\nOptions:\n{listing}\n\n"
              f"Title: {title}\nContent:\n{(text or '')[:3000]}")
    try:
        raw = client.complete(prompt, system="You reply with a NAICS sector code only.")
    except Exception:
        return base
    picked = (raw or "").strip().split()[0].strip(".:") if raw else ""
    if picked not in SECTORS:
        return base
    return Classification(
        code=picked, sector=SECTORS[picked], confidence="medium",
        score=base.score, margin=base.margin,
        evidence=base.evidence + [f"llm:{picked}"],
        runners_up=base.runners_up, method="keyword+llm")
