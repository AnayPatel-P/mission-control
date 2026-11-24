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
