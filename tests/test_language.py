"""Language hint and 'must use a tool' rule of the agent."""
import pytest

from app.agent.language import guess_language, looks_like_job_request, reply_hint
from tests.test_agent import agent, calls, kinds, says


@pytest.mark.parametrize("text,expected", [
    ("Zoek een stage in Antwerpen", "nl"),
    ("Welke vacature past het best bij mij?", "nl"),
    ("Chercher un job étudiant à Liège", "fr"),
    ("Quel emploi me convient le mieux ?", "fr"),
    ("Find student jobs in Brussels and tell me which one fits my CV best", "en"),
    ("Is there a job as an astronaut in Mechelen?", "en"),
    ("Найди подработку в Брюсселе", "ru"),
    ("hi", None),
    ("", None),
])
def test_guess_language_on_short_messages(text, expected):
    assert guess_language(text) == expected


def test_reply_hint_only_for_other_languages_than_english():
    assert reply_hint("Zoek een stage in Antwerpen") == "\n\n(Reply in Dutch.)"
    assert "French" in reply_hint("Chercher un job étudiant à Liège") and "Russian" in reply_hint("Найди работу")
    assert reply_hint("Find me a job") == "" and reply_hint("hi") == ""


@pytest.mark.parametrize("text,expected", [
    ("Zoek een stage in Antwerpen", True), ("Chercher un job étudiant à Liège", True), ("Find student jobs", True),
    ("Welke vacature past bij mij?", True), ("Найди подработку", True), ("Are there openings in Gent?", True),
    ("What can you do?", False), ("hello", False), ("Thanks!", False),
])
def test_looks_like_job_request(text, expected):
    assert looks_like_job_request(text) is expected


# ------------------------------------------------------------- in the agent loop
def test_the_model_is_told_which_language_to_answer_in_but_the_history_is_clean():
    a, client = agent(says("Hier zijn ze."))
    list(a.run("Hallo, wat kan je voor mij doen?"))
    sent = client.requests[0]["messages"]
    assert sent[-1]["content"].endswith("(Reply in Dutch.)")                 # the model sees the hint
    assert a.history[0] == {"role": "user", "content": "Hallo, wat kan je voor mij doen?"}   # the user's words only


def test_english_questions_get_no_hint():
    a, client = agent(says("Hello!"))
    list(a.run("hi there"))
    assert client.requests[0]["messages"][-1]["content"] == "hi there"


def test_answering_a_vacancy_question_from_memory_is_discarded_once():
    a, client = agent(says("Astronaut jobs are rare."), calls("search_jobs", {"keywords": "astronaut"}),
                      says("Nothing found."))
    events = list(a.run("Is there a job as an astronaut in Mechelen?"))
    assert kinds(events) == ["token", "retract", "tool_call", "tool_result", "token", "done"]
    assert events[1]["reason"] == "The model answered without searching."
    assert "STOP" in client.requests[1]["messages"][-1]["content"]
    assert events[-1]["text"] == "Nothing found." and "Astronaut jobs are rare" not in str(a.history)


def test_the_tool_rule_is_applied_only_once_so_the_model_cannot_loop():
    a, _ = agent(says("From memory 1."), says("From memory 2."))
    events = list(a.run("Find me a job"))
    assert kinds(events).count("retract") == 1 and events[-1]["text"] == "From memory 2."


def test_questions_that_are_not_about_vacancies_are_never_nudged():
    a, client = agent(says("I can search vacancies."))
    events = list(a.run("What can you do?"))
    assert "retract" not in kinds(events) and len(client.requests) == 1


def test_an_answer_after_a_tool_call_is_not_nudged():
    a, _ = agent(calls("search_jobs", {}), says("Here is one (id: t:1)."))
    assert "retract" not in kinds(list(a.run("Find me a job")))
