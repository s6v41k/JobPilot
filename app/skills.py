"""A small multilingual skill vocabulary (English / Dutch / French).

Job ads and CVs name the same skill in different languages ("klantendienst",
"service client", "customer service"). We map every spelling to ONE canonical skill,
so "matched / missing skills" works across languages without calling an LLM.
Plain keyword rules: easy to read, extend and explain.
"""
from __future__ import annotations

import re

from app.geo import normalize

# canonical skill -> spellings (lowercase, accents optional; matched on word boundaries)
SKILLS: dict[str, list[str]] = {
    # --- service, retail, hospitality ---
    "customer service": ["customer service", "customer care", "klantendienst", "klantenservice", "klantvriendelijk",
                         "service client", "relation client", "accueil des clients", "onthaal"],
    "cash handling": ["cash register", "cashier", "kassa", "kassamedewerker", "caisse", "caissier", "caissiere",
                      "cash handling", "encaissement"],
    "hospitality": ["waiter", "waitress", "bartender", "barista", "horeca", "ober", "barman", "serveur", "serveuse",
                    "restaurant", "brasserie", "cafe", "keuken", "cuisine", "cook", "kok", "cuisinier", "snackbar"],
    "retail": ["retail", "shop assistant", "winkel", "verkoper", "verkoopster", "magasin", "vendeur", "vendeuse",
               "winkelbediende", "supermarket", "supermarkt", "supermarche"],
    "sales": ["sales", "verkoop", "vente", "commercial", "salesperson", "account manager"],
    "cleaning": ["cleaning", "cleaner", "schoonmaak", "poetshulp", "nettoyage", "femme de menage", "ontsmetting"],
    "warehouse": ["warehouse", "magazijn", "entrepot", "order picking", "orderpicker", "logistics", "logistiek",
                  "logistique", "packing", "inpakken"],
    "driving": ["driving licence", "driver's licence", "rijbewijs", "permis de conduire", "chauffeur", "driver",
                "delivery", "levering", "livreur", "bestuurder"],
    "forklift": ["forklift", "heftruck", "reachtruck", "cariste", "chariot elevateur"],
    "childcare": ["childcare", "babysit", "babysitting", "kinderopvang", "garde d'enfants", "creche", "jeugdbeweging",
                  "monitor", "animateur"],
    "healthcare": ["nurse", "nursing", "verpleeg", "infirmier", "infirmiere", "aide-soignant", "zorg", "soins",
                   "patient", "first aid", "eerste hulp", "premiers secours", "caregiver"],
    "teaching": ["teaching", "teacher", "tutoring", "tutor", "leerkracht", "bijles", "enseignant", "professeur",
                 "cours particuliers"],
    "events": ["event", "events", "evenement", "evenementen", "festival", "stewarding", "steward", "hostess",
               "animation"],
    "administration": ["administration", "administrative", "administratie", "administratief", "secretary",
                       "secretaresse", "secretariat", "secretaire", "data entry", "receptionist", "reception"],
    "construction": ["construction", "bouw", "chantier", "carpenter", "timmerman", "schilder", "painter", "plumber",
                     "loodgieter", "electrician", "elektricien", "electricien", "welder", "lasser", "soudeur"],
    "agriculture": ["agriculture", "landbouw", "tuinbouw", "horticulture", "gardening", "tuinman", "jardinier",
                    "fruit picking", "plukken", "cueillette"],
    # --- office and IT ---
    "excel": ["excel", "spreadsheet"],
    "office suite": ["microsoft office", "ms office", "word", "powerpoint", "office 365", "google workspace"],
    "python": ["python"],
    "java": ["java"],
    "javascript": ["javascript", "typescript", "node.js", "nodejs", "react", "vue", "angular"],
    "web development": ["html", "css", "web development", "webontwikkeling", "frontend", "front-end", "backend",
                        "back-end", "php", "wordpress", "fastapi", "django", "flask"],
    "sql": ["sql", "mysql", "postgresql", "sqlite", "database", "databank", "base de donnees"],
    "git": ["git", "github", "gitlab", "version control"],
    "linux": ["linux", "unix", "bash"],
    "cloud": ["aws", "azure", "google cloud", "docker", "kubernetes", "devops"],
    "data analysis": ["data analysis", "data analyst", "data science", "power bi", "tableau", "statistics",
                      "statistiek", "statistiques", "machine learning", "analytics"],
    "software testing": ["pytest", "unit test", "unit testing", "testing", "qa engineer", "test automation"],
    "it support": ["helpdesk", "it support", "servicedesk", "technical support", "hardware", "netwerk", "network"],
    "design": ["photoshop", "illustrator", "figma", "graphic design", "grafisch", "graphisme", "canva", "ux", "ui"],
    "social media": ["social media", "instagram", "tiktok", "facebook", "linkedin", "community management",
                     "content creation", "reseaux sociaux", "sociale media"],
    "marketing": ["marketing", "seo", "copywriting", "communicatie", "communication", "newsletter", "campaign"],
    "accounting": ["accounting", "boekhouding", "comptabilite", "bookkeeping", "invoicing", "facturatie", "payroll"],
    "project management": ["project management", "projectmanagement", "scrum", "agile", "gestion de projet"],
    "ai": ["artificial intelligence", "kunstmatige intelligentie", "intelligence artificielle", "llm", "chatbot"],
    # --- soft skills ---
    "teamwork": ["teamwork", "team player", "teamspirit", "samenwerken", "esprit d'equipe", "travail en equipe",
                 "collaboration"],
    "flexibility": ["flexible", "flexibel", "flexibilite", "flexi-job", "flexijob", "weekend", "avond", "soir"],
    "stress resistance": ["under pressure", "stressbestendig", "stress resistant", "resistant au stress", "druk"],
    "initiative": ["initiative", "initiatief", "proactive", "proactief", "zelfstandig", "autonomous", "autonome"],
    "communication skills": ["communication skills", "communicatief", "communicatieve vaardigheden", "communicatif"],
    "punctuality": ["punctual", "punctueel", "ponctuel", "reliable", "betrouwbaar", "fiable"],
}

_COMPILED: dict[str, re.Pattern] = {}


def _pattern(skill: str) -> re.Pattern:
    if skill not in _COMPILED:
        words = sorted((normalize(w) for w in SKILLS[skill]), key=len, reverse=True)
        # word boundaries stop "java" matching "javascript" and "ui" matching "quit"
        _COMPILED[skill] = re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])")
    return _COMPILED[skill]


def find_skills(text: str) -> set[str]:
    """All canonical skills mentioned anywhere in `text` (any of the three languages)."""
    haystack = normalize(text or "")
    return {skill for skill in SKILLS if _pattern(skill).search(haystack)}


# ------------------------------------------------------------------ languages
_LANG_NAMES = {
    "nl": ("dutch", "nederlands", "neerlandais", "flemish", "vlaams"),
    "fr": ("french", "frans", "francais"),
    "en": ("english", "engels", "anglais"),
}
_LEVELS = (
    (3, ("native", "moedertaal", "maternelle", "mother tongue", "fluent", "vloeiend", "courant", "advanced",
         "gevorderd", "avance", "c1", "c2", "bilingual", "tweetalig", "bilingue")),
    (1, ("basic", "beginner", "elementary", "basis", "beginner", "notions", "debutant", "a1", "a2")),
    (2, ("intermediate", "gemiddeld", "intermediaire", "b1", "b2", "good", "goed", "bon", "conversational")),
)


def language_levels(languages: list[str]) -> dict[str, int]:
    """{'nl': 3, 'en': 3, 'fr': 2} from strings like 'Dutch (native)', 'French - intermediate'.
    Level 3 = native/fluent, 2 = intermediate (also the default when no level is written), 1 = basic."""
    levels: dict[str, int] = {}
    for entry in languages or []:
        text = normalize(entry)
        for code, names in _LANG_NAMES.items():
            if any(re.search(rf"(?<![a-z]){n}(?![a-z])", text) for n in names):
                level = next((lv for lv, words in _LEVELS if any(re.search(rf"(?<![a-z0-9]){w}(?![a-z0-9])", text) for w in words)), 2)
                levels[code] = max(level, levels.get(code, 0))
    return levels
