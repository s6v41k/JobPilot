import pytest

from app.geo import normalize, region_from_area
from app.providers.base import detect_language, infer_experience, infer_job_type


def test_normalize_strips_accents():
    assert normalize("Liège") == "liege"


@pytest.mark.parametrize("area,region", [
    (["België", "Vlaams-Brabant (Provincie)", "Leuven"], "flanders"),
    (["Belgium", "Walloon Brabant", "Wavre"], "wallonia"),
    (["Belgique", "Liège (Province)", "Liège"], "wallonia"),
    (["België", "Brussel (Hoofdstedelijk Gewest)"], "brussels"),
    (["België"], None),
    ([], None),
])
def test_region_from_area(area, region):
    assert region_from_area(area) == region


@pytest.mark.parametrize("title,expected", [
    ("Stage marketing", "internship"),
    ("Internship Data Analyst", "internship"),
    ("Dringend studenten gezocht", "student"),
    ("Étudiant(e) polyvalent(e)", "student"),
    ("Backstage crew", "full-time"),          # "stage" inside a word is not an internship
    ("Elektricien", "full-time"),
])
def test_infer_job_type(title, expected):
    assert infer_job_type(title) == expected


def test_part_time_from_contract():
    assert infer_job_type("Kapper", "", "part_time") == "part-time"


def test_infer_experience_and_language():
    assert infer_experience("Junior developer") == "junior"
    assert infer_experience("Developer") is None
    assert detect_language("Wij zoeken een medewerker voor de afdeling met ervaring in het vak") == "nl"
    assert detect_language("Nous recherchons un employé pour le poste avec une expérience dans la vente") == "fr"
    assert detect_language("We are looking for a person with experience to join our team and the staff") == "en"
    assert detect_language("hi") is None
