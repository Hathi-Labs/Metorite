"""The staging copy holds the allowlisted organizations, and nothing else.

WS-38 T-9, ``project-docs/specs/staging_and_daily_release.md`` §3 and §5.10.
``infra/staging/minimise.sql`` runs on a restore of the production dump, on the
production box, before the copy travels to the staging box.

R8: this runs the real file with ``psql`` against a production-SHAPED database:
the whole ladder plus the four H3 phases, which is where the
``organization_id`` columns of production come from. Each test works on its
own clone (``CREATE DATABASE … TEMPLATE``), because the file deletes.

What it fences:

1. A customer that is not on the list leaves no row in any org-scoped table.
2. The kept organization keeps its rows.
3. An identity survives only for a member of a kept organization.
4. It refuses, and changes nothing, on an unknown or empty slug list.
5. It refuses a new table that has no ``organization_id`` and no rule.
6. It refuses a credential-shaped column that still holds a value.
7. It leaves no web push subscription, no plugin auth and no hash of a
   WhatsApp link or reply code, and it closes each open WhatsApp act
   (LD-8c). Step 6 also matches ``p256dh``, ``auth`` (the whole name only),
   ``code_hash``, ``encryption_key`` and ``token`` (a part of a name).
"""

from __future__ import annotations

import itertools
import json
import pathlib
import subprocess
import uuid

import pytest
from sqlalchemy import create_engine, text

from tests.unit.test_h3_rls_promotion_rehearsal import _DB_GATE, promoted  # noqa: F401

pytestmark = _DB_GATE

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SQL = _ROOT / "infra/staging/minimise.sql"
_n = itertools.count()


@pytest.fixture
def clone(promoted):  # noqa: F811
    """A throwaway copy of the promoted database, as a superuser engine."""
    base = promoted.admin_engine.url
    name = f"{base.database}_mz{next(_n)}"
    promoted.admin_engine.dispose()
    maint = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with maint.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        # A template must have no session. The fixture's own pool is disposed
        # above, and a straggler (a previous clone's engine) is ended here.
        c.execute(text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": base.database})
        c.execute(text(f'CREATE DATABASE "{name}" TEMPLATE "{base.database}"'))
    eng = create_engine(base.set(database=name))
    eng.dsn = base.set(database=name).render_as_string(hide_password=False).replace("+psycopg", "")
    eng.org_a, eng.org_b = promoted.org_a, promoted.org_b
    try:
        yield eng
    finally:
        eng.dispose()
        with maint.connect() as c:
            c.execute(text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :d AND pid <> pg_backend_pid()"), {"d": name})
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        maint.dispose()


def _run(eng, slugs: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", eng.dsn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-v", f"keep_slugs={slugs}",
         "-f", str(_SQL)],
        capture_output=True, text=True, timeout=300,
    )


def _org_rows(eng, org: str) -> int:
    """Rows of ``org`` across every org-scoped public table."""
    with eng.connect() as c:
        tables = c.execute(text(
            "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relkind IN ('r','p') AND NOT c.relispartition "
            "AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid "
            "AND a.attname = 'organization_id' AND NOT a.attisdropped)")).scalars().all()
        return sum(
            c.execute(text(f'SELECT count(*) FROM public."{t}" WHERE organization_id = :o'),
                      {"o": org}).scalar_one()
            for t in tables
        )


def _orgs(eng) -> set[str]:
    with eng.connect() as c:
        return set(c.execute(text("SELECT slug FROM organization")).scalars())


def test_only_the_kept_organization_remains(clone) -> None:
    before_a, before_b = _org_rows(clone, clone.org_a), _org_rows(clone, clone.org_b)
    assert before_a > 0 and before_b > 0, "the fixture seeds rows for both organizations"
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _orgs(clone) == {"h3rls-a"}
    assert _org_rows(clone, clone.org_b) == 0, "a customer not on the list left rows behind"
    assert _org_rows(clone, clone.org_a) == before_a, "a kept organization lost rows"
    assert "kept h3rls-a" in res.stdout


def test_an_identity_survives_only_for_a_kept_member(clone) -> None:
    keep_id, drop_id = str(uuid.uuid4()), str(uuid.uuid4())
    with clone.begin() as c:
        c.execute(text("INSERT INTO user_identity (id, email) VALUES (:k, :ke), (:d, :de)"),
                  {"k": keep_id, "ke": "owner@h3rls.test", "d": drop_id, "de": "gone@other.test"})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        left = set(c.execute(text("SELECT email FROM user_identity")).scalars())
    assert "owner@h3rls.test" in left, "the kept organization's member lost the identity"
    assert "gone@other.test" not in left


@pytest.mark.parametrize("slugs", ["h3rls-a,no-such-org", "", "nope"],
                         ids=["one-unknown", "empty", "all-unknown"])
def test_a_bad_slug_list_refuses_and_changes_nothing(clone, slugs: str) -> None:
    before, orgs = _org_rows(clone, clone.org_b), _orgs(clone)
    res = _run(clone, slugs)
    assert res.returncode != 0
    assert _orgs(clone) == orgs
    assert _org_rows(clone, clone.org_b) == before


def test_an_unclassified_table_refuses(clone) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_new_global (id int, note text)"))
    orgs = _orgs(clone)
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_new_global" in res.stderr
    assert _orgs(clone) == orgs, "the refusal must roll everything back"


def test_a_credential_left_behind_refuses(clone) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_cred (organization_id uuid, api_key text)"))
        c.execute(text("INSERT INTO public.zz_cred VALUES (:o, 'sk-live-x')"), {"o": clone.org_a})
    orgs = _orgs(clone)
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_cred.api_key" in res.stderr
    assert _orgs(clone) == orgs


def test_the_named_credentials_are_blanked(clone) -> None:
    """Every credential column the file names exists on the production shape.
    A rename would turn its UPDATE into an error, so this also catches drift."""
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        for table in ("agent_token", "provider_keys", "agent_client", "auth_email_otp_token",
                      "email_detached_purges"):
            assert c.execute(text(f"SELECT count(*) FROM public.{table}")).scalar_one() == 0, table


def test_the_assistant_profile_is_blanked(clone) -> None:
    """WS-63 PZ-9 (``personalization.md`` §7 point 5): the member's profile
    is their own words, and no copy holds it. The row and its other columns
    stay. Mutation: drop the UPDATE in ``minimise.sql``."""
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO user_settings (organization_id, user_id, assistant_profile, "
            "number_system) VALUES (:o, 'pz9@h3rls.test', "
            "CAST(:p AS jsonb), 'international')"),
            {"o": clone.org_a, "p": '{"v": 1, "facts": [{"id": "f1", "text": "x"}]}'})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        row = c.execute(text(
            "SELECT assistant_profile, number_system FROM user_settings "
            "WHERE user_id = 'pz9@h3rls.test'")).one()
    assert row == (None, "international")


def test_a_varchar_credential_refuses(clone) -> None:
    """format_type prints `character varying(255)`, so a type-name match missed it."""
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_v (organization_id uuid, foo_secret varchar(255))"))
        c.execute(text("INSERT INTO public.zz_v VALUES (:o, 's3cr3t')"), {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_v.foo_secret" in res.stderr


def test_the_legacy_mcp_auth_is_blanked(clone) -> None:
    """mcp_servers.headers and env_vars hold PLAINTEXT auth until a server moves
    to the encrypted registry. A verifier proved they left production."""
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO mcp_servers (name, label, organization_id, url, headers, env_vars) "
            "VALUES ('zz', 'zz', :o, 'https://mcp.example.test', '{\"Authorization\": \"Bearer sk-live-abcdefghijklmnop\"}', "
            "'{\"API\": \"k\"}')"), {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        row = c.execute(text("SELECT headers::text, env_vars::text FROM mcp_servers "
                             "WHERE name = 'zz'")).one()
    assert row == ("{}", "{}")


def test_the_push_subscriptions_are_emptied(clone) -> None:
    """LD-8c. A subscription holds the endpoint of a real device and its two
    keys. No row may leave, in a kept organization too. Mutation: drop the
    TRUNCATE in ``minimise.sql``, and step 6 refuses on ``p256dh``."""
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO push_subscriptions (organization_id, member_email, endpoint, "
            "p256dh, auth) VALUES (:o, 'push@h3rls.test', "
            "'https://push.example.test/ld8c-1', 'BLd8cPublicKey', 'ld8cAuthSecret')"),
            {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        assert c.execute(text("SELECT count(*) FROM push_subscriptions")).scalar_one() == 0


def test_the_plugin_auth_and_the_code_hashes_are_blanked(clone) -> None:
    """LD-8c. ``plugins.auth_config`` is NOT NULL, so it becomes ``{}``. The
    install route (``gateway/routes/integrations.py``) writes manifest.auth
    to ``auth_config`` and the whole manifest to ``manifest``, so the seed
    puts the same auth object in both, and the ``auth`` key must go from
    ``manifest`` too. ``whatsapp_member_links.code_hash`` becomes ``''``,
    because a pending row must hold a hash.
    ``whatsapp_pending_acts.confirm_code_hash`` becomes NULL, because its
    CHECK takes NULL or 64 hex characters only. Each row stays. Mutation:
    drop one SET. A plugin assertion fails, or step 6 refuses on a code
    hash."""
    code_hash = "a" * 64
    auth = {"type": "service_http", "authorization_type": "bearer",
            "verification_tokens": {"openai": "plain-plugin-key"}}
    manifest = {"schema_version": "v1", "name_for_model": "ld8c", "auth": auth,
                "api": {"type": "openapi", "url": "https://plugin.example.test/openapi.yaml"}}
    with clone.begin() as c:
        c.execute(text(
            "INSERT INTO plugins (organization_id, name, label, manifest_url, auth_type, "
            "auth_config, manifest) VALUES (:o, 'ld8c-plugin', 'LD-8c', "
            "'https://plugin.example.test/ai-plugin.json', 'service_http', "
            "CAST(:a AS jsonb), CAST(:m AS jsonb))"),
            {"o": clone.org_a, "a": json.dumps(auth), "m": json.dumps(manifest)})
        link = c.execute(text(
            "INSERT INTO whatsapp_member_links (organization_id, member_email, status, "
            "code_hash, code_expires_at) VALUES (:o, 'wa@h3rls.test', 'pending', :h, "
            "now() + interval '15 minutes') RETURNING id"),
            {"o": clone.org_a, "h": code_hash}).scalar_one()
        c.execute(text("INSERT INTO chat_session (id, organization_id) VALUES ('ld8c-chat', :o)"),
                  {"o": clone.org_a})
        c.execute(text(
            "INSERT INTO whatsapp_pending_acts (organization_id, member_email, wa_id, "
            "chat_session_id, link_id, tool_name, arguments, card_text, expires_at, "
            "confirm_code_hash) VALUES (:o, 'wa@h3rls.test', '919900000001', 'ld8c-chat', "
            ":l, 'send_email', CAST('{}' AS jsonb), 'Send it?', now() + interval '1 hour', :h)"),
            {"o": clone.org_a, "l": link, "h": code_hash})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        plugin = c.execute(text(
            "SELECT auth_config::text, manifest FROM plugins WHERE name = 'ld8c-plugin'")).one()
        link_row = c.execute(text(
            "SELECT code_hash, status FROM whatsapp_member_links WHERE id = :l"),
            {"l": link}).one()
        act_hash = c.execute(text(
            "SELECT confirm_code_hash FROM whatsapp_pending_acts WHERE link_id = :l"),
            {"l": link}).scalar_one()
    assert plugin[0] == "{}", plugin[0]
    assert "auth" not in plugin[1], plugin[1]
    assert "plain-plugin-key" not in json.dumps(plugin[1]), plugin[1]
    # The rest of the manifest stays: only the auth goes.
    assert plugin[1] == {k: v for k, v in manifest.items() if k != "auth"}, plugin[1]
    assert tuple(link_row) == ("", "pending"), link_row
    assert act_hash is None, act_hash


def test_each_open_whatsapp_act_closes(clone) -> None:
    """LD-8c, review finding 3. A NULL ``confirm_code_hash`` turns an email
    act into one that a plain Confirm answers. So a ``pending`` or
    ``running`` act becomes ``expired``, a closed state of 242, and a closed
    act keeps its state. Mutation: drop the ``state`` SET, and the pending
    act stays open."""
    code_hash = "b" * 64
    with clone.begin() as c:
        link = c.execute(text(
            "INSERT INTO whatsapp_member_links (organization_id, member_email, status, "
            "code_hash, code_expires_at) VALUES (:o, 'acts@h3rls.test', 'pending', :h, "
            "now() + interval '15 minutes') RETURNING id"),
            {"o": clone.org_a, "h": code_hash}).scalar_one()
        c.execute(text("INSERT INTO chat_session (id, organization_id) VALUES ('ld8c-acts', :o)"),
                  {"o": clone.org_a})
        for state in ("pending", "running", "done", "cancelled"):
            c.execute(text(
                "INSERT INTO whatsapp_pending_acts (organization_id, member_email, wa_id, "
                "chat_session_id, link_id, tool_name, arguments, card_text, state, "
                "offered_at, offered_wamid, expires_at, confirm_code_hash) VALUES (:o, "
                "'acts@h3rls.test', '919900000002', 'ld8c-acts', :l, 'send_email', "
                "CAST('{}' AS jsonb), :card, :s, now(), :w, now() + interval '1 hour', :h)"),
                {"o": clone.org_a, "l": link, "card": f"act {state}", "s": state,
                 "w": f"wamid.ld8c.{state}", "h": code_hash})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr
    with clone.connect() as c:
        rows = dict(c.execute(text(
            "SELECT card_text, state FROM whatsapp_pending_acts WHERE link_id = :l"),
            {"l": link}).all())
        hashes = c.execute(text(
            "SELECT count(*) FROM whatsapp_pending_acts WHERE confirm_code_hash IS NOT NULL")
        ).scalar_one()
    assert rows == {"act pending": "expired", "act running": "expired",
                    "act done": "done", "act cancelled": "cancelled"}, rows
    assert hashes == 0


@pytest.mark.parametrize("column", ["p256dh", "auth", "reset_code_hash"])
def test_a_push_key_or_a_code_hash_left_behind_refuses(clone, column: str) -> None:
    """LD-8c: step 6 matches the three new names. A new column of that name
    fails the export until it is blanked above."""
    with clone.begin() as c:
        c.execute(text(f"CREATE TABLE public.zz_k (organization_id uuid, {column} text)"))
        c.execute(text("INSERT INTO public.zz_k VALUES (:o, 'held')"), {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert f"zz_k.{column}" in res.stderr


@pytest.mark.parametrize("column", [
    "access_token", "auth_token", "id_token", "bot_token", "encryption_key"])
def test_a_token_or_a_key_left_behind_refuses(clone, column: str) -> None:
    """LD-8c, review finding 4. Step 6 matches ``token`` as a part of a name,
    and ``encryption_key``. Before, only four token names matched, so an
    ``access_token`` with a value left in the copy. Mutation: put the four
    token names back in place of ``token``, and the token cases fail."""
    with clone.begin() as c:
        c.execute(text(f"CREATE TABLE public.zz_t (organization_id uuid, {column} text)"))
        c.execute(text("INSERT INTO public.zz_t VALUES (:o, 'ya29.held-value')"),
                  {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert f"zz_t.{column}" in res.stderr


def test_a_name_that_only_starts_with_auth_passes(clone) -> None:
    """``^auth$`` is a whole name. ``author`` and ``auth_type`` hold no key,
    so a value in them must not stop the export."""
    with clone.begin() as c:
        c.execute(text(
            "CREATE TABLE public.zz_a (organization_id uuid, author text, auth_type text)"))
        c.execute(text("INSERT INTO public.zz_a VALUES (:o, 'Ada', 'oauth2')"),
                  {"o": clone.org_a})
    res = _run(clone, "h3rls-a")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize("value", [
    '{"auth": "Bearer abcdefghijklmnopqrstu"}', '{"k": "sk-ABCDEFGHIJKLMNOPQRSTUVWX"}',
    '{"k": "ghp_ABCDEFGHIJKLMNOPQRSTUV"}', '{"k": "AKIAABCDEFGHIJKLMNOP"}',
    '{"k": "-----BEGIN RSA PRIVATE KEY-----"}'],
    ids=["bearer", "sk", "github", "aws", "pem"])
def test_a_token_shaped_json_value_refuses(clone, value: str) -> None:
    with clone.begin() as c:
        c.execute(text("CREATE TABLE public.zz_j (organization_id uuid, meta jsonb)"))
        c.execute(text("INSERT INTO public.zz_j VALUES (:o, CAST(:v AS jsonb))"),
                  {"o": clone.org_a, "v": value})
    res = _run(clone, "h3rls-a")
    assert res.returncode != 0
    assert "zz_j.meta" in res.stderr
