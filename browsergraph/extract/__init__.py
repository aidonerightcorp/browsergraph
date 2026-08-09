"""Deterministic extraction — patterns, links/frontier, page content."""
from browsergraph.extract.content import Page, looks_like_article, parse_page, text_of
from browsergraph.extract.links import Frontier, Robots, links_from_html, normalize, same_site
from browsergraph.extract.patterns import (
    Address,
    Contacts,
    Phone,
    addresses,
    emails,
    extract_contacts,
    phones,
    socials,
)

__all__ = ["Page", "parse_page", "looks_like_article", "text_of", "Frontier", "Robots",
           "links_from_html", "normalize", "same_site", "Address", "Contacts", "Phone",
           "addresses", "emails", "extract_contacts", "phones", "socials"]
