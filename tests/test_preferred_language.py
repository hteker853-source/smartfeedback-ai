"""Tests for customers.preferred_language: write-once persistence, and the
LLM-driven (never hardcoded-template) call-script generation in app/calle.py."""

from app.calle import CalleService
from app.pipeline import Pipeline


def test_preferred_language_write_once(repo, settings):
    p = Pipeline(repo, settings)
    customer = repo.upsert_customer(p.business_id, "+905551112233")
    assert customer.get("preferred_language") is None

    changed = repo.set_preferred_language(customer["id"], "en")
    assert changed is True
    after = repo.get_customer(p.business_id, "+905551112233")
    assert after["preferred_language"] == "en"

    # A second detection must NEVER overwrite the first one.
    changed_again = repo.set_preferred_language(customer["id"], "de")
    assert changed_again is False
    still = repo.get_customer(p.business_id, "+905551112233")
    assert still["preferred_language"] == "en"


def test_migration_adds_column_without_data_loss(repo, settings):
    # The `repo`/`settings` fixtures already run init_db (including the
    # additive migration); this just asserts the column is queryable and
    # defaults to NULL for a freshly created customer.
    business = repo.get_or_create_business(settings.business_name)
    customer = repo.upsert_customer(business["id"], "+905550001111")
    assert "preferred_language" in customer
    assert customer["preferred_language"] is None


def test_build_task_falls_back_without_llm(settings):
    # `settings` fixture has no AWS/DeepSeek keys -> get_llm() is None ->
    # build_task must return the single neutral fallback sentence, never
    # raise, and never depend on a hardcoded per-language dict.
    calle = CalleService(settings)
    task = calle.build_task("Acme", "tr", preferred_language="fr")
    assert "Acme" in task
    assert task  # non-empty, deterministic, no network call attempted


def test_build_task_uses_llm_when_available(settings, monkeypatch):
    """With an LLM provider present, build_task must use the LLM-generated
    text (not the fallback), and must pass the preferred_language through."""
    import app.calle as calle_mod

    settings.deepseek_api_key = "fake-key-for-test"

    captured = {}

    def _fake_complete_json(llm, system, user):
        captured["system"] = system
        captured["user"] = user
        return {"task": "Bonjour, ceci est un test genere par le LLM."}

    monkeypatch.setattr(calle_mod, "complete_json", _fake_complete_json)

    svc = CalleService(settings)
    task = svc.build_task(
        "Acme", "tr", preferred_language="fr", context="cold fries"
    )
    assert task == "Bonjour, ceci est un test genere par le LLM."
    assert "fr" in captured["user"]
    assert "cold fries" in captured["user"]


def test_build_task_falls_back_when_llm_call_fails(settings, monkeypatch):
    """If the LLM call itself fails/returns nothing usable, build_task must
    still return the safe fallback sentence rather than raising or returning
    an empty string."""
    import app.calle as calle_mod

    settings.deepseek_api_key = "fake-key-for-test"
    monkeypatch.setattr(calle_mod, "complete_json", lambda *a, **k: None)

    svc = CalleService(settings)
    task = svc.build_task("Acme", "tr", preferred_language="fr")
    assert "Acme" in task
