"""The task library.

Each task is a crawl plus a specific extraction. They share the crawler,
politeness and provenance handling from `base`, so a new task is usually only
its extraction logic.
"""
from __future__ import annotations

import re
from typing import ClassVar

from browsergraph.classify.naics import classify as naics_classify
from browsergraph.classify.naics import refine_with_llm
from browsergraph.extract.content import Page, looks_like_article
from browsergraph.extract.patterns import Contacts, extract_contacts
from browsergraph.tasks.base import CRAWL_PARAMS, Task, TaskResult, register

# Pages most likely to carry contact and identity information.
_HIGH_VALUE = re.compile(
    r"/(contact|about|team|people|staff|impressum|legal|imprint|"
    r"locations?|offices?|support|help|company)\b", re.I)


@register
class Spider(Task):
    """Enumerate a site's pages."""

    name: ClassVar[str] = "spider"
    summary: ClassVar[str] = "Crawl a site and map its pages, titles and links."
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS + [
        {"name": "allow", "required": False, "description": "regex a URL must match"},
        {"name": "deny", "required": False, "description": "regex a URL must not match"},
    ]

    def execute(self, browser) -> TaskResult:
        allow = re.compile(self.values["allow"]) if self.values.get("allow") else None
        deny = re.compile(self.values["deny"]) if self.values.get("deny") else None
        crawler = self.crawler(browser, allow=allow, deny=deny)

        pages, visited = [], []
        for page, depth in crawler.pages():
            visited.append(page.url)
            pages.append({**page.to_dict(), "depth": depth,
                          "outlinks": len(page.links)})

        return TaskResult(
            task=self.name, ok=bool(pages), pages_visited=visited,
            data={"pages": pages,
                  "sitemap": sorted({p["url"] for p in pages})},
            stats=crawler.report(),
            warnings=([] if pages else ["no pages fetched"]),
            error="" if pages else (crawler.stats.stopped_reason or "no pages fetched"),
        )


@register
class ContactHarvest(Task):
    """Collect public contact details."""

    name: ClassVar[str] = "contacts"
    summary: ClassVar[str] = ("Extract public emails, phone numbers, addresses and "
                              "social profiles from a site.")
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS + [
        {"name": "prioritise_contact_pages", "type": "bool", "required": False,
         "default": True},
    ]

    def execute(self, browser) -> TaskResult:
        crawler = self.crawler(browser)
        merged = Contacts()
        per_page, visited = [], []

        for page, _ in crawler.pages():
            visited.append(page.url)
            found = extract_contacts(page.text, page.links, page.mailtos)
            if not found.empty:
                per_page.append({"url": page.url, **found.to_dict()})
                merged = merged.merge(found)
            # contact/about pages usually link to the rest of the identity set
            if self.values.get("prioritise_contact_pages", True):
                for link in page.links:
                    if _HIGH_VALUE.search(link):
                        crawler.frontier.push(link, 0)

        warnings = []
        if merged.empty:
            warnings.append("no contact details found — the site may render them "
                            "in images or behind a form")
        return TaskResult(
            task=self.name, ok=not merged.empty, pages_visited=visited,
            data={"contacts": merged.to_dict(), "by_page": per_page},
            stats=crawler.report(), warnings=warnings,
        )


@register
class NewsPull(Task):
    """Collect news and blog articles."""

    name: ClassVar[str] = "news"
    summary: ClassVar[str] = "Find and extract news, blog or press articles from a site."
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS + [
        {"name": "since", "required": False, "description": "ISO date lower bound"},
        {"name": "include_text", "type": "bool", "required": False, "default": False},
    ]

    def execute(self, browser) -> TaskResult:
        crawler = self.crawler(browser)
        since = self.values.get("since") or ""
        articles, visited, skipped = [], [], 0

        for page, _ in crawler.pages():
            visited.append(page.url)
            if not looks_like_article(page):
                skipped += 1
                continue
            if since and page.published and page.published < since:
                skipped += 1
                continue
            item = {"url": page.url, "title": page.title,
                    "published": page.published,
                    "summary": page.description or page.text[:280],
                    "word_count": page.word_count}
            if self.values.get("include_text"):
                item["text"] = page.text
            articles.append(item)

        articles.sort(key=lambda a: (a["published"] or ""), reverse=True)
        warnings = []
        if not articles and visited:
            warnings.append("no articles matched — the site may paginate its "
                            "index or render listings client-side")
        return TaskResult(
            task=self.name, ok=bool(articles), pages_visited=visited,
            data={"articles": articles, "count": len(articles)},
            stats={**crawler.report(), "skipped_non_article": skipped},
            warnings=warnings,
        )


@register
class SiteResearch(Task):
    """Profile a site: identity, contacts, content and classification."""

    name: ClassVar[str] = "research"
    summary: ClassVar[str] = ("Profile a website — identity, contact details, "
                              "content sample, socials and NAICS sector.")
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS

    def execute(self, browser) -> TaskResult:
        crawler = self.crawler(browser)
        merged = Contacts()
        first: Page | None = None
        visited, titles, corpus = [], [], []

        for page, _ in crawler.pages():
            visited.append(page.url)
            first = first or page
            titles.append(page.title)
            corpus.append(f"{page.title}\n{page.description}\n{page.text[:4000]}")
            merged = merged.merge(extract_contacts(page.text, page.links, page.mailtos))

        if not visited:
            return TaskResult(task=self.name, ok=False, stats=crawler.report(),
                              error=crawler.stats.stopped_reason or "no pages fetched")

        text = "\n\n".join(corpus)
        naics = naics_classify(text, title=(first.title if first else ""),
                               url=self.values["url"])
        return TaskResult(
            task=self.name, ok=True, pages_visited=visited,
            data={
                "identity": {
                    "url": self.values["url"],
                    "name": (first.title if first else "").split("|")[0].strip(),
                    "description": first.description if first else "",
                    "language": first.lang if first else "",
                },
                "contacts": merged.to_dict(),
                "naics": naics.to_dict(),
                "page_titles": titles[:25],
                "structured_data": (first.structured[:3] if first else []),
            },
            stats=crawler.report(),
            warnings=([] if naics.usable else
                      [f"NAICS confidence {naics.confidence} — treat as unclassified"]),
        )


@register
class NaicsClassify(Task):
    """Classify a business into a NAICS sector."""

    name: ClassVar[str] = "naics"
    summary: ClassVar[str] = "Classify a business into a NAICS sector from its website."
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS + [
        {"name": "use_llm", "type": "bool", "required": False, "default": False,
         "description": "refine a weak keyword result with a model"},
    ]

    def __init__(self, llm_client=None, **values):
        super().__init__(**values)
        self.llm_client = llm_client

    def execute(self, browser) -> TaskResult:
        crawler = self.crawler(browser)
        visited, corpus, title = [], [], ""

        for page, _ in crawler.pages():
            visited.append(page.url)
            title = title or page.title
            corpus.append(f"{page.title} {page.description} {page.text[:4000]}")

        if not visited:
            return TaskResult(task=self.name, ok=False, stats=crawler.report(),
                              error=crawler.stats.stopped_reason or "no pages fetched")

        text = "\n".join(corpus)
        result = naics_classify(text, title=title, url=self.values["url"])
        if self.values.get("use_llm") and not result.usable:
            result = refine_with_llm(result, text, self.llm_client, title=title)

        warnings = []
        if not result.usable:
            warnings.append(
                f"confidence {result.confidence} (score {result.score:.1f}, "
                f"margin {result.margin:.1f}) — not reliable enough to assign a code")
        return TaskResult(
            task=self.name, ok=result.usable, pages_visited=visited,
            data={"naics": result.to_dict()},
            stats=crawler.report(), warnings=warnings,
        )


@register
class PublicData(Task):
    """Everything public, in one pass."""

    name: ClassVar[str] = "public_data"
    summary: ClassVar[str] = ("Single-pass harvest: contacts, addresses, socials, "
                              "articles, structured data and NAICS.")
    param_spec: ClassVar[list[dict]] = CRAWL_PARAMS

    def execute(self, browser) -> TaskResult:
        crawler = self.crawler(browser)
        merged = Contacts()
        visited, articles, structured, corpus = [], [], [], []
        title = ""

        for page, _ in crawler.pages():
            visited.append(page.url)
            title = title or page.title
            corpus.append(f"{page.title} {page.description} {page.text[:3000]}")
            merged = merged.merge(extract_contacts(page.text, page.links, page.mailtos))
            structured.extend(page.structured[:2])
            if looks_like_article(page):
                articles.append({"url": page.url, "title": page.title,
                                 "published": page.published})

        if not visited:
            return TaskResult(task=self.name, ok=False, stats=crawler.report(),
                              error=crawler.stats.stopped_reason or "no pages fetched")

        text = "\n".join(corpus)
        naics = naics_classify(text, title=title, url=self.values["url"])
        return TaskResult(
            task=self.name, ok=True, pages_visited=visited,
            data={"contacts": merged.to_dict(),
                  "articles": sorted(articles, key=lambda a: a["published"] or "",
                                     reverse=True)[:50],
                  "naics": naics.to_dict(),
                  "structured_data": structured[:10]},
            stats=crawler.report(),
            warnings=([] if naics.usable else ["NAICS not confidently determined"]),
        )
