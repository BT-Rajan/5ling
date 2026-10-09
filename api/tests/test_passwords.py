from __future__ import annotations

from app.auth import passwords


def test_length_bounds() -> None:
    assert passwords.check_policy("a1b2c3d4e5f") != []  # 11
    assert passwords.check_policy("a1b2c3d4e5f6") == []  # 12
    assert passwords.check_policy("x1" * 65) != []  # 130


def test_no_composition_rules_so_passphrases_work() -> None:
    assert passwords.check_policy("correct horse battery staple") == []


def test_repetitive_and_common_passwords_refused() -> None:
    assert passwords.check_policy("aaaaaaaaaaaa") != []
    assert passwords.check_policy("passwordpassword") != []
    assert passwords.check_policy("PasswordPassword") != []
    assert passwords.check_policy("my-ledgerline-pass") != []


def test_personal_details_refused_but_short_fragments_allowed() -> None:
    assert passwords.check_policy("priyanka-2026-ok", email="priyanka@firm.in") != []
    assert passwords.check_policy("raj-and-the-ledger", email="raj@firm.in") == []
    assert passwords.check_policy("with-sharma-inside", full_name="Anita Sharma") != []


def test_hash_is_argon2id_and_verifies() -> None:
    stored = passwords.hash_password("correct horse battery")
    assert stored.startswith("$argon2id$")
    assert passwords.verify_password(stored, "correct horse battery")
    assert not passwords.verify_password(stored, "correct horse batterY")


def test_garbage_hash_never_verifies_or_raises() -> None:
    assert not passwords.verify_password("not-a-hash", "anything")
    assert not passwords.verify_password("", "anything")


def test_unicode_forms_are_treated_as_the_same_password() -> None:
    stored = passwords.hash_password("\uff21bcdefghijkl1")  # full-width A
    assert passwords.verify_password(stored, "Abcdefghijkl1")


def test_temporary_passwords_are_valid_unique_and_unambiguous() -> None:
    seen = {passwords.generate_temporary_password() for _ in range(200)}
    assert len(seen) == 200
    for temp in seen:
        assert passwords.check_policy(temp) == []
        assert len(temp) == 19
        assert not set(temp.replace("-", "")) & set("0o1li")
