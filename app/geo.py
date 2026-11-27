"""Small geography helpers for Belgium (no network, no external data).

Belgium has three regions. Job sites usually tell us the province or city, so we map
that to one of: "flanders", "wallonia", "brussels".
"""
from __future__ import annotations

import unicodedata

REGIONS = ("flanders", "wallonia", "brussels")


def normalize(text: str) -> str:
    """Lowercase and strip accents, so 'Liège' == 'liege'."""
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


# Province names in NL / FR / EN -> region. "brabant" alone is ambiguous, so the
# longer, more specific names come first (see region_from_area).
_PROVINCE_REGION: list[tuple[str, str]] = [
    ("vlaams-brabant", "flanders"), ("vlaams brabant", "flanders"), ("flemish brabant", "flanders"),
    ("brabant flamand", "flanders"),
    ("waals-brabant", "wallonia"), ("waals brabant", "wallonia"), ("walloon brabant", "wallonia"),
    ("brabant wallon", "wallonia"),
    ("oost-vlaanderen", "flanders"), ("east flanders", "flanders"), ("flandre orientale", "flanders"),
    ("west-vlaanderen", "flanders"), ("west flanders", "flanders"), ("flandre occidentale", "flanders"),
    ("antwerpen", "flanders"), ("antwerp", "flanders"), ("anvers", "flanders"),
    ("limburg", "flanders"), ("limbourg", "flanders"),
    ("vlaanderen", "flanders"), ("flanders", "flanders"), ("flandre", "flanders"),
    ("henegouwen", "wallonia"), ("hainaut", "wallonia"),
    ("luik", "wallonia"), ("liege", "wallonia"),
    ("namen", "wallonia"), ("namur", "wallonia"),
    ("luxembourg", "wallonia"), ("luxemburg", "wallonia"),
    ("wallonie", "wallonia"), ("wallonia", "wallonia"), ("wallonne", "wallonia"),
    ("brussel", "brussels"), ("bruxelles", "brussels"), ("brussels", "brussels"),
]


# The same city has different names in Dutch / French / English. Job sites use the local
# one, but students type whatever they know ("Antwerp", "Ghent", "Louvain").
_CITY_GROUPS = [
    ["Antwerpen", "Anvers", "Antwerp"], ["Gent", "Gand", "Ghent"], ["Brugge", "Bruges"],
    ["Leuven", "Louvain"], ["Mechelen", "Malines"], ["Oostende", "Ostende", "Ostend"],
    ["Kortrijk", "Courtrai"], ["Liege", "Luik"], ["Mons", "Bergen"], ["Namur", "Namen"],
    ["Tournai", "Doornik"], ["Ieper", "Ypres"], ["Nivelles", "Nijvel"], ["Wavre", "Waver"],
    ["Aalst", "Alost"], ["Tongeren", "Tongres"], ["Hasselt"], ["Genk"], ["Turnhout"],
    ["Roeselare", "Roulers"], ["Dendermonde", "Termonde"], ["Charleroi"], ["Verviers"],
    ["Arlon", "Aarlen"], ["Eupen"], ["Lier", "Lierre"], ["Vilvoorde", "Vilvorde"],
]
_CITY_LOOKUP = {normalize(name): group for group in _CITY_GROUPS for name in group}

# "Brussels" is not one municipality but 19 (Ixelles, Schaerbeek...): treat it as a region.
BRUSSELS_NAMES = {"brussels", "brussel", "bruxelles", "brussels city", "brussel-stad", "bruxelles-ville",
                  "brussels-capital", "brussels capital region"}


def is_brussels(city: str | None) -> bool:
    return normalize(city or "") in BRUSSELS_NAMES


def city_variants(city: str) -> list[str]:
    """All known spellings of a city (NL/FR/EN); just the input if we do not know it."""
    return list(_CITY_LOOKUP.get(normalize(city), [city.strip()]))


def region_from_area(area: list[str] | None) -> str | None:
    """Find the region from a list like ["Belgium", "Vlaams-Brabant", "Leuven"].

    Returns None when nothing matches (we never guess).
    """
    for part in area or []:
        name = normalize(part)
        for key, region in _PROVINCE_REGION:
            if key in name:
                return region
    return None
