"""Tiny keyword translator for searching in a multilingual country.

Vacancy titles are written in Dutch or French, but a student may type English.
For the most common job words we also search the NL/FR equivalents.
(A plain dictionary: easy to read, extend and explain.)
"""
from __future__ import annotations

from app.geo import normalize

# English word -> Dutch and French equivalents used in Belgian job titles.
SYNONYMS: dict[str, list[str]] = {
    "waiter": ["ober", "serveur"], "waitress": ["serveuse", "ober"],
    "bartender": ["barman", "barista"], "barista": ["barista"],
    "cashier": ["kassamedewerker", "caissier", "kassa"],
    "cleaner": ["schoonmaker", "nettoyage", "poetsvrouw"],
    "cook": ["kok", "cuisinier"], "chef": ["kok", "chef"],
    "driver": ["chauffeur", "bestuurder"], "delivery": ["levering", "livreur"],
    "seller": ["verkoper", "vendeur"], "sales": ["verkoop", "vente", "commercial"],
    "salesperson": ["verkoper", "vendeur"], "shop": ["winkel", "magasin"],
    "warehouse": ["magazijn", "entrepôt"], "teacher": ["leerkracht", "enseignant"],
    "nurse": ["verpleegkundige", "infirmier"], "accountant": ["boekhouder", "comptable"],
    "developer": ["ontwikkelaar", "développeur"], "programmer": ["programmeur", "développeur"],
    "engineer": ["ingenieur", "ingénieur"], "designer": ["ontwerper", "designer"],
    "marketing": ["marketing"], "assistant": ["assistent", "assistant"],
    "receptionist": ["onthaal", "réceptionniste"], "secretary": ["secretaresse", "secrétaire"],
    "mechanic": ["mecanicien", "mécanicien"], "electrician": ["elektricien", "électricien"],
    "student": ["student", "étudiant"], "internship": ["stage", "stagiair"],
    "intern": ["stagiair", "stagiaire"], "babysitter": ["babysit", "garde d'enfants"],
    "gardener": ["tuinman", "jardinier"], "hotel": ["hotel", "hôtel"],
    "customer": ["klant", "client"], "support": ["support", "helpdesk"],
    "data": ["data", "données"], "analyst": ["analist", "analyste"],
    "administrative": ["administratief", "administratif"], "manager": ["manager", "responsable"],
}


def expand_keywords(keywords: str, max_variants: int = 3) -> list[str]:
    """Return the original keyword first, then up to max_variants-1 translations.

    Only single-word queries are expanded: for longer phrases a mixed-language
    guess would just add noise.
    """
    text = (keywords or "").strip()
    variants = [text]
    word = normalize(text)
    if " " not in word:
        for synonym in SYNONYMS.get(word, []):
            if synonym not in variants:
                variants.append(synonym)
    return variants[:max_variants]
