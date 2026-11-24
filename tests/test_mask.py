import random
import string

from mission_control.mask import mask_text

M = "•••masked•••"


def test_known_keys():
    secrets = [
        "sk-ant-api03-" + "A" * 40,
        "ghp_" + "b" * 36,
        "AKIA" + "Q" * 16,
        "xoxb-" + "1234567890-" + "c" * 20,
        "-----BEGIN PRIVATE KEY-----\n" + "d" * 60 + "\n-----END PRIVATE KEY-----",
    ]
    for s in secrets:
        out = mask_text(f"before {s} after")
        assert out == f"before {M} after", s


def test_env_file_values():
    out = mask_text("API_KEY=abc123\nDEBUG=true", ".env.local")
    assert out == f"API_KEY={M}\nDEBUG={M}"


def test_assignments_in_code():
    assert mask_text('password = "hunter2hunter2"') == f'password = "{M}"'
    assert mask_text('{"token": "x9y8z7w6v5"}') == f'{{"token": "{M}"}}'


def _rand_b64(n):
    rng = random.Random(0)
    alphabet = string.ascii_letters + string.digits + "+/"
    return "".join(rng.choice(alphabet) for _ in range(n))


def test_high_entropy():
    assert mask_text(f"x {_rand_b64(40)} y") == f"x {M} y"
    for safe in [
        "The quick brown fox jumps over the dog",
        "123e4567-e89b-12d3-a456-426614174000",
        "9569b07a1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f",
    ]:
        assert mask_text(safe) == safe


def test_note_and_bash():
    out = mask_text("export OPENAI_API_KEY=sk-proj-" + "e" * 30)
    assert out == f"export OPENAI_API_KEY={M}"


def test_ordinary_code_untouched():
    for code in [
        "token = tokenize(x)",
        "password_hash = hash_password(pw)",
        "handleSessionTimelineScrubberChangeEventListener",
        "SUPER_LONG_CONSTANT_NAME_FOR_THE_TIMELINE_SCRUBBER",
    ]:
        assert mask_text(code) == code


def test_more_ordinary_code():
    for code in [
        "handleOAuth2SessionTimelineScrubberChangeEvent",
        "src/components/session/TimelineScrubber2/Index",
        "sorted(xs, key=len)",
        "f(token=token)",
    ]:
        assert mask_text(code) == code


def test_unquoted_secrets():
    cases = {
        "export DB_PASSWORD=hunter2abc": "hunter2abc",
        "curl -u admin:s3cretpass https://x": "s3cretpass",
        "password: hunter2abc": "hunter2abc",
        "Authorization: Bearer abcDEF123456": "abcDEF123456",
        "DATABASE_URL=postgres://u:p4ss@h/db": "p4ss",
        'password: str = "changeme"': "changeme",
    }
    for text, secret in cases.items():
        out = mask_text(text)
        assert M in out and secret not in out, text


def test_env_line_structure_preserved():
    assert mask_text("API_KEY=abc123\n\nDEBUG=true\n", ".env") == f"API_KEY={M}\n\nDEBUG={M}\n"


def test_brackets_and_numbers_kept():
    for code in [
        "client.create(max_tokens=1024)",
        "f(max_tokens=4096)",
        "call(x, retries=3)",
        "token = tokenize(x)",
        "password_hash = hash_password(pw)",
    ]:
        assert mask_text(code) == code
