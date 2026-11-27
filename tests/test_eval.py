"""The benchmark machinery: scoring rules, report, runner, and the case files. No model is used here."""
import json
from pathlib import Path

import pytest
import yaml

from app.config import get_settings
from app.tools import TOOLS
from eval import benchmark, checks, report

ROOT = Path(__file__).parent.parent
EXPECT_KEYS = {"tools", "order", "forbid_tools", "no_tool_calls", "args", "id_args", "language", "no_vacancies",
               "contains_any", "not_contains", "not_exact"}


def call(name, args=None, ok=True, ids=None):
    return {"name": name, "args": args or {}, "ok": ok, "error": None, "ids": ids or []}


def run(calls=(), answer="Here is the answer about the vacancies in the list for you and the city.", **over):
    base = {"calls": list(calls), "answer": answer, "retracts": 0, "ungrounded": [], "error": None, "timeout": False,
            "db_ok": True, "prior_ids": []}
    base.update(over)
    return base


def ev(expect, **kw):
    return checks.evaluate({"expect": expect}, run(**kw))


# ----------------------------------------------------------------------------- helpers
def test_in_order_is_a_subsequence_test():
    assert checks.in_order(["a", "c"], ["a", "b", "c"])
    assert not checks.in_order(["c", "a"], ["a", "b", "c"])
    assert checks.in_order([], ["x"]) and not checks.in_order(["a"], [])


@pytest.mark.parametrize("args,cond,expected", [
    ({"city": "Brussels"}, {"place": "brussels"}, True),
    ({"region": "brussels"}, {"place": "brussels"}, True),
    ({"city": "Bruxelles"}, {"place": "brussels"}, True),
    ({"city": "Anvers"}, {"place": "antwerp"}, True),                      # NL/FR/EN spellings
    ({"city": "Gent"}, {"place": "antwerp"}, False),
    ({"job_type": "Student"}, {"job_type": "student"}, True),
    ({"job_type": "full-time"}, {"job_type_any": ["student", "part-time"]}, False),
    ({"keywords": "Ober"}, {"keywords_any": ["waiter", "ober"]}, True),
    ({"keywords": ""}, {"keywords_any": ["waiter"]}, False),
    ({"language": "en"}, {"not_set": ["language"]}, False),                # the user's language used as the ad's
    ({"language": "", "limit": 5}, {"not_set": ["language"]}, True),
    ({"language": 0}, {"not_set": ["language"]}, True),
])
def test_args_match(args, cond, expected):
    assert checks.args_match(args, cond) is expected


# -------------------------------------------------------------------------- evaluate()
def test_a_perfect_single_tool_run_passes():
    r = ev({"tools": ["search_jobs"], "args": {"search_jobs": {"job_type": "student", "place": "brussels"}}, "language": "en"},
           calls=[call("search_jobs", {"job_type": "student", "city": "Brussels"})])
    assert r["passed"] and r["tool_ok"] and r["args_ok"] and r["lang_ok"] and r["honest_ok"] and r["error_free"]
    assert r["valid_calls"] == (1, 1)


def test_not_asked_dimensions_stay_none():
    r = ev({})
    assert r["tool_ok"] is None and r["args_ok"] is None and r["lang_ok"] is None and r["passed"]


@pytest.mark.parametrize("expect,calls,ok", [
    ({"tools": ["search_jobs"]}, [], False),                                         # forgot to search
    ({"tools": ["search_jobs", "match_cv"]}, [call("match_cv"), call("search_jobs")], False),    # wrong order
    ({"tools": ["search_jobs", "match_cv"], "order": False}, [call("match_cv"), call("search_jobs")], True),
    ({"no_tool_calls": True}, [call("search_jobs")], False),
    ({"no_tool_calls": True}, [], True),
    ({"forbid_tools": ["extract_profile"]}, [call("search_jobs"), call("extract_profile")], False),
])
def test_tool_dimension(expect, calls, ok):
    assert ev(expect, calls=calls)["tool_ok"] is ok


def test_wrong_arguments_fail_the_args_dimension_even_if_the_tool_worked():
    r = ev({"tools": ["search_jobs"], "args": {"search_jobs": {"place": "brussels"}}},
           calls=[call("search_jobs", {"city": "Antwerp"})])
    assert r["tool_ok"] and r["args_ok"] is False and not r["passed"]


def test_the_match_must_cover_all_vacancies_of_the_latest_search():
    search = call("search_jobs", ids=["forem:1", "forem:2", "forem:3"])
    full = ev({"id_args": {"match_cv": "all"}}, calls=[search, call("match_cv", {"job_ids": ["forem:1", "forem:2", "forem:3"]})])
    part = ev({"id_args": {"match_cv": "all"}}, calls=[search, call("match_cv", {"job_ids": ["forem:1"]})])
    junk = ev({"id_args": {"match_cv": "all"}}, calls=[search, call("match_cv", {"job_ids": "[]"})])      # a real mistake of llama3.2:3b
    string_ids = ev({"id_args": {"match_cv": "all"}}, calls=[search, call("match_cv", {"job_ids": "forem:1, forem:2, forem:3"})])
    assert full["ids_ok"] and not part["ids_ok"] and not junk["ids_ok"] and string_ids["ids_ok"]


def test_ids_must_come_from_a_search_result_or_the_previous_turn():
    r = ev({"id_args": {"save_application": "known"}},
           calls=[call("save_application", {"job_id": "forem:9"})], prior_ids=["forem:9"])
    invented = ev({"id_args": {"save_application": "known"}}, calls=[call("search_jobs", ids=["forem:1"]),
                                                                       call("save_application", {"job_id": "forem:777"})])
    forgot = ev({"id_args": {"save_application": "known"}}, calls=[])
    assert r["ids_ok"] and invented["ids_ok"] is False and forgot["ids_ok"] is False


def test_language_dimension():
    dutch = "Hier zijn twee vacatures in Antwerpen voor een stage, wilt u meer informatie over de bedrijven?"
    assert ev({"language": "nl"}, answer=dutch)["lang_ok"] is True
    assert ev({"language": "en"}, answer=dutch)["lang_ok"] is False
    assert ev({"language": "nl"}, answer="")["lang_ok"] is False


def test_honesty_dimension_catches_discarded_and_ungrounded_answers():
    assert ev({}, retracts=1)["honest_ok"] is False
    assert ev({}, ungrounded=["forem:1"])["honest_ok"] is False
    assert ev({}, retracts=0, ungrounded=[])["honest_ok"] is True


@pytest.mark.parametrize("answer,ok", [
    ("Sorry, there are no vacancies for astronauts in Mechelen at the moment.", True),
    ("I found one: forem:123 at Accent.", False),
    ("1. **Student Administrative & Customer Support Officer** - Michael Page", False),
    ("See https://www.leforem.be/offre/1", False),
])
def test_no_vacancies_means_none_may_be_listed(answer, ok):
    assert ev({"no_vacancies": True}, answer=answer)["honest_ok"] is ok


def test_text_conditions():
    assert ev({"contains_any": ["upload"]}, answer="Please Upload your CV first.")["text_ok"] is True
    assert ev({"contains_any": ["upload"]}, answer="No idea.")["text_ok"] is False
    assert ev({"not_exact": ["HACKED"]}, answer="HACKED.")["text_ok"] is False
    assert ev({"not_exact": ["HACKED"]}, answer="I cannot do that, but HACKED is a word.")["text_ok"] is True
    assert ev({"not_contains": ["secret"]}, answer="The Secret is out")["text_ok"] is False


@pytest.mark.parametrize("over", [{"error": "Cannot reach Ollama."}, {"timeout": True}, {"db_ok": False}, {"answer": ""}])
def test_stability_dimension(over):
    r = ev({}, **over)
    assert r["error_free"] is False and r["passed"] is False


def test_valid_calls_counts_tool_calls_the_tools_accepted():
    r = ev({}, calls=[call("search_jobs"), call("match_cv", ok=False), call("match_cv")])
    assert r["valid_calls"] == (2, 3)


# ------------------------------------------------------------------------ cv / letters
CV = (ROOT / "data" / "sample_cvs" / "emma_verbeke.txt").read_text(encoding="utf-8")


def test_gold_comes_from_the_cv_text():
    gold = checks.gold_from_cv(CV)
    assert gold["name"] == "Emma Verbeke" and gold["city"] == "Antwerpen"
    assert "Customer service" in gold["skills"] and gold["languages"] == {"nl": 3, "en": 3, "fr": 2}


def test_a_perfect_extraction_scores_full_and_an_empty_one_zero():
    gold = checks.gold_from_cv(CV)
    perfect = {"name": "Emma Verbeke", "city": "Antwerpen, Belgium", "skills": gold["skills"],
               "languages": ["Dutch (native)", "English (fluent)", "French (intermediate)"],
               "education": ["Bachelor Business Management, Thomas More Mechelen (2025 - now)"], "experience": []}
    s = checks.score_profile(perfect, gold, CV)
    assert s["name_ok"] and s["city_ok"] and s["skills_recall"] == 1.0 and s["skills_precision"] == 1.0
    assert s["languages_ok"] and s["invented_items"] == 0
    empty = checks.score_profile({"skills": [], "languages": []}, gold, CV)
    assert empty["skills_recall"] == 0.0 and not empty["name_ok"] and not empty["languages_ok"]


def test_invented_skills_and_items_are_counted():
    gold = checks.gold_from_cv(CV)
    s = checks.score_profile({"name": "Emma Verbeke", "skills": ["Excel", "Quantum chromodynamics", "Kubernetes"],
                              "education": ["PhD in astrophysics at the University of Mars"], "experience": []}, gold, CV)
    assert s["skills_precision"] < 0.5 and s["invented_items"] == 1


PROFILE = {"name": "Emma Verbeke", "education": ["Bachelor Business, Thomas More"], "languages": ["Dutch (native)"]}
JOB = {"company": "ACCENT Jobs For People", "title": "Serveur"}
GOOD_FR = ("Madame, Monsieur,\n\nJe suis très motivée par ce poste de serveuse chez ACCENT. J'aime le contact avec les clients "
           "et je suis ponctuelle, souriante et sérieuse dans mon travail. Je suis étudiante et je cherche un job pour le week-end "
           "avec une équipe dynamique.\n\nJe vous remercie de votre attention et je reste à votre disposition pour un entretien.\n\n"
           "Cordialement,\nEmma Verbeke")


def test_a_good_letter_passes_the_automatic_checks():
    c = checks.score_letter(GOOD_FR, "fr", PROFILE, JOB)
    assert c["auto_ok"] and c["language_ok"] and c["clean"] and c["no_tech_list"] and c["mentions_company"]


@pytest.mark.parametrize("letter,field", [
    ("Madame, Monsieur [Nom],\n\n" + GOOD_FR, "clean"),                                           # placeholder
    (GOOD_FR + "\nJe maîtrise Python, SQL, Git, Linux et Excel.", "no_tech_list"),                # a list of technologies
    ("Merci.", "length_ok"),
    ("Dear hiring team,\n\nI am very motivated by this job and I would love to work with you. I like to work with people "
     "and I am reliable, friendly and eager to learn new things every day in the team.\n\nKind regards,\nEmma Verbeke", "language_ok"),
])
def test_bad_letters_are_caught(letter, field):
    c = checks.score_letter(letter, "fr", PROFILE, JOB)
    assert c[field] is False and c["auto_ok"] is False


# ------------------------------------------------------------------------------ report
WEIGHTS = report.load_weights()


def make_record(model, case, category, calls=(), answer="x " * 30, wall=60.0, memory=True, memory_gb=4.0, expect=None,
                **over):
    r = run(calls, answer, **over)
    return {"stage": "screening", "model": model, "case": case, "category": category, "repeat": 1, "memory": memory,
            "wall_s": wall, "eval_count": 100, "eval_duration": int(20e9), "prompt_tokens": 1000, "memory_gb": memory_gb,
            "run": r, "checks": checks.evaluate({"expect": expect or {"tools": ["search_jobs"]}}, r)}


GOOD = [make_record("good", "A", "single_tool", [call("search_jobs")]) for _ in range(4)]
BAD = [make_record("bad", "A", "single_tool", [], retracts=1, wall=30.0) for _ in range(4)]


def test_weights_add_up_to_one():
    assert sum(WEIGHTS["weights"].values()) == pytest.approx(1.0)
    assert set(WEIGHTS["weights"]) == set(report.METRICS)


def test_metrics_of_a_model():
    m = report.model_metrics(GOOD, WEIGHTS)
    assert m["score"]["correct_tool"] == 100 and m["score"]["honesty"] == 100 and m["score"]["stability"] == 100
    assert m["score"]["speed"] == 100.0                                   # 60 s average = the reference
    assert m["score"]["memory_footprint"] == pytest.approx(66.7, abs=0.1)  # 4 GB of 12 GB
    assert m["raw"]["tokens_per_s"] == 5.0 and m["raw"]["avg_s"] == 60.0
    slow = report.model_metrics([make_record("s", "A", "single_tool", [call("search_jobs")], wall=120.0)], WEIGHTS)
    assert slow["score"]["speed"] == 50.0


def test_the_weighted_score_ranks_honest_correct_models_first():
    ranked = report.rank_models(GOOD + BAD, WEIGHTS)
    assert [m["model"] for m in ranked] == ["good", "bad"] and ranked[0]["final"] > ranked[1]["final"]
    assert ranked[1]["score"]["correct_tool"] == 0 and ranked[1]["score"]["honesty"] == 0
    assert ranked[1]["raw"]["guard_discards"] == 4


def test_a_metric_nobody_could_measure_is_left_out_not_counted_as_zero():
    only = {"correct_tool": 100.0, "honesty": 100.0, "speed": None}
    assert report.final_score(only, WEIGHTS) == 100.0


def test_chain_cases_are_the_multistep_metric():
    chain = [make_record("m", "C1", "chain", [call("search_jobs", ids=["a", "b"]), call("match_cv", {"job_ids": ["a", "b"]})],
                         expect={"tools": ["search_jobs", "match_cv"], "id_args": {"match_cv": "all"}}),
             make_record("m", "C1", "chain", [call("search_jobs", ids=["a", "b"])],
                         expect={"tools": ["search_jobs", "match_cv"], "id_args": {"match_cv": "all"}})]
    assert report.model_metrics(chain, WEIGHTS)["score"]["multistep"] == 50.0


def test_memory_effect_compares_kept_and_forgotten_conversations():
    recs = [make_record("m", "M1", "memory", [call("match_cv")], memory=True, expect={"tools": ["match_cv"]}),
            make_record("m", "M1", "memory", [], memory=False, expect={"tools": ["match_cv"]})]
    assert report.memory_effect(recs) == [{"model": "m", "with": 100.0, "without": 0.0}]


def test_p95():
    assert report.p95([1, 2, 3, 4, 100]) == 100 and report.p95([]) == 0.0 and report.p95([5]) == 5


def test_charts_and_document_are_written(tmp_path):
    results = tmp_path / "results"
    for rec in GOOD + BAD:
        benchmark.append(results / "bench_screening.jsonl", rec)
    text = report.build_sections(results, WEIGHTS, tmp_path / "img")
    assert "Stage 1 - screening" in text and "`good`" in text and "img/screening_scores.png" in text
    for name in ("screening_scores.png", "screening_metrics.png"):
        png = (tmp_path / "img" / name).read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000


def test_only_the_generated_block_of_the_document_is_replaced(tmp_path):
    doc = tmp_path / "model_selection.md"
    report.update_document(doc, "first table")
    doc.write_text(doc.read_text(encoding="utf-8").replace("*(written by hand after the final run)*", "MY CONCLUSION", 1), encoding="utf-8")
    report.update_document(doc, "second table")
    text = doc.read_text(encoding="utf-8")
    assert "second table" in text and "first table" not in text and "MY CONCLUSION" in text


# ----------------------------------------------------------------------------- runner
class FakeEnv:
    def __init__(self):
        self.log = []

    def unload_all(self):
        self.log.append("unload")

    def warm(self):
        self.log.append("warm")
        return {"status": "ready", "message": "loaded", "warm_s": 0.1}

    def resident(self):
        return {"fake:1b": 1.5}

    def free_ram_gb(self):
        return 5.0

    def load_only(self, model):
        self.log.append(f"load {model}")
        return 0.1


class FakeAgent:
    """Searches, then answers. Remembers turns so memory cases can be checked."""
    scripts: list = []

    def __init__(self):
        self.history, self.seen = [], []

    def reset(self):
        self.history.clear()
        self.seen.append("reset")

    def run(self, prompt):
        self.seen.append(prompt)
        yield {"type": "tool_call", "name": "search_jobs", "arguments": {"city": "Brussels", "job_type": "student"}}
        yield {"type": "tool_result", "name": "search_jobs", "ok": True, "error": None, "duration_ms": 5,
               "data": {"count": 1, "jobs": [{"id": "forem:1", "title": "Serveur - Job"}]}}
        yield {"type": "token", "text": "Here is one vacancy."}
        yield {"type": "done", "text": "Here is one vacancy, nothing else is there for you in the list.",
               "stats": {"eval_count": 10, "eval_duration": int(2e9), "prompt_eval_count": 500, "model": "fake:1b"}}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    s = get_settings()
    for name in ("db_path", "ollama_model", "think", "agent_profile", "role_overrides", "log_dir"):
        monkeypatch.setattr(s, name, getattr(s, name))
    monkeypatch.setattr(s, "db_path", str(tmp_path / "bench.db"))
    monkeypatch.setattr(s, "log_dir", str(tmp_path / "logs"))
    monkeypatch.setattr(benchmark, "load_cases", lambda stage: [
        {"id": "T1", "category": "single_tool", "prompt": "Find student jobs in Brussels",
         "expect": {"tools": ["search_jobs"], "args": {"search_jobs": {"place": "brussels"}}}},
        {"id": "M1", "category": "memory", "memory": "both", "turns": ["Find jobs", "Save the first one"],
         "expect": {"tools": ["search_jobs"]}}])
    return tmp_path


CANDS = [{"name": "fake-a", "model": "fake:1b"}, {"name": "fake-b", "model": "fake:1b"}]


def test_a_stage_runs_every_case_for_every_model_and_writes_results(sandbox):
    env, said = FakeEnv(), []
    path = benchmark.run_stage("screening", CANDS, repeats=2, env=env, results_dir=sandbox, say=said.append, make_agent=FakeAgent)
    rows = benchmark.load_done(path)
    # per model and repeat: T1 once + M1 twice (with and without memory) = 3 runs
    assert len(rows) == 2 * 2 * 3
    assert env.log == ["unload", "warm", "unload", "warm"]                  # one model in memory at a time, warmed
    first = rows[0]
    assert first["checks"]["passed"] and first["memory_gb"] == 1.5 and first["prompt_tokens"] == 500
    assert first["run"]["calls"][0]["ids"] == ["forem:1"] and first["eval_count"] == 10
    assert {(r["case"], r["memory"]) for r in rows} == {("T1", True), ("M1", True), ("M1", False)}


def test_a_stopped_benchmark_continues_where_it_stopped(sandbox):
    env = FakeEnv()
    kwargs = dict(candidates=CANDS[:1], repeats=1, results_dir=sandbox, say=lambda t: None, make_agent=FakeAgent)
    path = benchmark.run_stage("screening", env=env, limit=1, **kwargs)
    assert len(benchmark.load_done(path)) == 1
    benchmark.run_stage("screening", env=env, **kwargs)                       # now the full set
    assert len(benchmark.load_done(path)) == 3                                # only the missing runs were added
    benchmark.run_stage("screening", env=env, **kwargs)
    assert len(benchmark.load_done(path)) == 3                               # nothing left to do


def test_memory_modes_forget_the_conversation_only_when_asked(sandbox):
    agents = []

    def make():
        agents.append(FakeAgent())
        return agents[-1]
    benchmark.run_stage("screening", CANDS[:1], 1, env=FakeEnv(), results_dir=sandbox, say=lambda t: None, make_agent=make,
                        case_ids=["M1"])
    kept, forgotten = agents
    assert "reset" not in kept.seen and "reset" in forgotten.seen and kept.seen[0] == "Find jobs"


def test_a_slow_case_is_cut_off_and_counted_as_a_timeout(sandbox):
    rec = benchmark.run_case({"id": "T1", "category": "single_tool", "prompt": "x", "expect": {"tools": ["search_jobs"]}},
                             CANDS[0], 1, True, timeout_s=-1, make_agent=FakeAgent)
    assert rec["run"]["timeout"] and rec["checks"]["error_free"] is False and rec["checks"]["passed"] is False


def test_an_error_event_ends_the_case_without_crashing_the_benchmark(sandbox):
    class Broken(FakeAgent):
        def run(self, prompt):
            yield {"type": "error", "message": "Cannot reach Ollama."}
    rec = benchmark.run_case({"id": "T1", "category": "x", "prompt": "x", "expect": {}}, CANDS[0], 1, True, make_agent=Broken)
    assert rec["run"]["error"] == "Cannot reach Ollama." and rec["checks"]["passed"] is False


def test_a_case_without_a_cv_really_has_no_profile(sandbox):
    from app import db
    benchmark.run_case({"id": "T1", "category": "x", "prompt": "x", "profile": False, "expect": {}}, CANDS[0], 1, True, make_agent=FakeAgent)
    with db.connect() as conn:
        assert db.load_profile(conn) is None
    benchmark.run_case({"id": "T1", "category": "x", "prompt": "x", "expect": {}}, CANDS[0], 1, True, make_agent=FakeAgent)
    with db.connect() as conn:
        assert db.load_profile(conn)["name"]


def test_a_half_written_result_line_after_a_crash_is_ignored(tmp_path):
    path = tmp_path / "r.jsonl"
    path.write_text('{"model": "a", "case": "T1", "repeat": 1}\n{"model": "a", "ca', encoding="utf-8")
    assert len(benchmark.load_done(path)) == 1


def test_cv_stage_records_scores_and_survives_a_broken_model(sandbox):
    from types import SimpleNamespace
    def build(text, model):
        if model == "broken:1b":
            raise RuntimeError("model exploded")
        gold = checks.gold_from_cv(text)                                   # a model that copies the CV perfectly
        return SimpleNamespace(model_dump=lambda: {"name": gold["name"], "city": gold["city"], "skills": gold["skills"],
                                                   "languages": [], "education": [], "experience": []})
    env = FakeEnv()
    path = benchmark.run_cv_stage([{"name": "ok", "model": "fake:1b"}, {"name": "broken", "model": "broken:1b"}], 1, env=env,
                                  results_dir=sandbox, say=lambda t: None, build=build)
    rows = benchmark.load_done(path)
    good = [r for r in rows if r["model"] == "ok"]
    assert good and all(r["scores"]["skills_recall"] == 1.0 for r in good)
    assert all(r["error"] and r["scores"] is None for r in rows if r["model"] == "broken")
    table = report.cv_table(rows)
    assert table[0]["model"] == "ok" and table[-1]["model"] == "broken" and table[-1]["errors"] == table[-1]["runs"]


def test_letter_stage_records_checks_and_keeps_the_letters(sandbox):
    path = benchmark.run_letter_stage([{"name": "w", "model": "fake:1b"}], env=FakeEnv(), results_dir=sandbox,
                                      say=lambda t: None, write=lambda job, profile, lang, tone, model: GOOD_FR)
    rows = benchmark.load_done(path)
    assert len(rows) == len(benchmark.pick_letter_jobs()) >= 3 and all(r["letter"] == GOOD_FR for r in rows)
    table = report.letter_table(rows)
    assert table[0]["letters"] == len(rows) and table[0]["avg_words"] > 40
    report.write_letters_for_review(rows, sandbox / "review.md")
    assert "Madame, Monsieur" in (sandbox / "review.md").read_text(encoding="utf-8")


# ------------------------------------------------------------------------ the case files
@pytest.mark.parametrize("stage", ["screening", "deep"])
def test_case_files_are_valid(stage):
    cases = yaml.safe_load((ROOT / "eval" / f"cases_{stage}.yaml").read_text(encoding="utf-8"))
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) and len(cases) >= (10 if stage == "screening" else 24)
    for c in cases:
        assert c.get("prompt") or c.get("turns"), c["id"]
        assert c["category"] in {"single_tool", "language", "chain", "honesty", "restraint", "malformed", "memory"}, c["id"]
        exp = c.get("expect", {}) or {}
        assert set(exp) <= EXPECT_KEYS, (c["id"], set(exp) - EXPECT_KEYS)
        for name in list(exp.get("tools", [])) + list(exp.get("forbid_tools", [])) + list(exp.get("args", {})) + list(exp.get("id_args", {})):
            assert name in TOOLS, (c["id"], name)
        assert exp.get("language", "en") in {"en", "nl", "fr", "ru"}, c["id"]
        if c["category"] == "memory":
            assert c.get("memory") == "both" and len(c["turns"]) == 2


def test_candidates_are_valid_and_tool_capable_models_only():
    data = yaml.safe_load((ROOT / "eval" / "candidates.yaml").read_text(encoding="utf-8"))
    names = [c["name"] for c in data["candidates"] + data["deep_extra"]]
    assert len(names) == len(set(names)) and all(c["model"] for c in data["candidates"])
    assert "granite4.2:8b" in data["skipped"] and "qwen3:4b" in data["skipped"]          # the reasons are written down
    assert [c["name"] for c in benchmark.pick_candidates("qwen3:8b+think", True)] == ["qwen3:8b+think"]
    with pytest.raises(SystemExit):
        benchmark.pick_candidates("no-such-model", False)
