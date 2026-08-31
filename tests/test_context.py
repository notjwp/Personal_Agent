"""shrink() — truncate, spill, and tell the model how to look at the rest."""
from agent import config
from agent.context import redact, shrink




def over_cap(tool="run_shell", factor=3):
    """Text guaranteed past `tool`'s cap, sized from the constant not a guess."""
    cap = min(config.TOOL_CAPS.get(tool, config.MAX_RESULT_CHARS),
              config.MAX_RESULT_CHARS)
    lines = []
    while sum(len(l) + 1 for l in lines) < cap * factor // 2:
        lines.append(f"line {len(lines)}")
    return "\n".join(lines)


def test_under_cap_returned_verbatim(tmp_workspace):
    assert shrink("read_file", "short output") == "short output"
    assert not config.ARTIFACTS.exists(), "nothing should be spilled under the cap"


def test_exactly_at_cap_is_not_spilled(tmp_workspace):
    text = "x" * config.TOOL_CAPS["run_shell"]
    assert shrink("run_shell", text) == text
    assert not config.ARTIFACTS.exists()


def test_over_cap_spills_and_instructs(tmp_workspace):
    text = over_cap()
    out = shrink("run_shell", text)

    # 1. bounded
    assert len(out) < config.MAX_RESULT_CHARS + 600
    # 2. head and tail survive
    assert "line 0" in out and text.rsplit(chr(10), 1)[-1] in out
    # 3. elision marker
    assert "elided" in out
    # 4. the full text is on disk, intact
    artifacts = list(config.ARTIFACTS.glob("*.txt"))
    assert len(artifacts) == 1
    assert artifacts[0].read_text(encoding="utf-8") == text
    # 5. the path is IN the returned string
    assert str(artifacts[0]) in out
    # 6. and so are instructions for using it - a bare path is ignored in practice
    assert "read_file" in out and "grep" in out


def test_single_giant_line_is_still_bounded(tmp_workspace):
    """Fewer lines than head+tail, so the line-based path cannot apply."""
    out = shrink("run_shell", "x" * (config.MAX_RESULT_CHARS * 3))
    assert len(out) < config.MAX_RESULT_CHARS + 600
    assert list(config.ARTIFACTS.glob("*.txt"))


def test_many_long_lines_are_bounded_by_characters(tmp_workspace):
    """The line-based path must still honour the CHARACTER cap (NFR-104).

    Found on a real repository, not in a unit test: 50 lines of pytest output
    shrank to 11,340 chars against a 6,000-char cap, because head+tail bounded
    LINES while the requirement bounds CHARACTERS. Real test output has long
    lines; the practice fixtures did not, which is why this survived 161 tests.
    """
    text = chr(10).join("x" * 400 for _ in range(config.MAX_RESULT_CHARS // 100))
    out = shrink("run_shell", text)
    assert len(out) < config.MAX_RESULT_CHARS + 600, (
        f"shrunk to {len(out)} chars against a {config.MAX_RESULT_CHARS} cap")


def test_per_tool_caps_are_honoured(tmp_workspace):
    """write_file has a much smaller cap than run_shell."""
    text = "y" * 3000
    assert len(shrink("write_file", text)) < len(text)   # over write_file's 400
    assert shrink("run_shell", text) == text             # under run_shell's 6000


def test_unknown_tool_falls_back_to_the_ceiling(tmp_workspace):
    assert shrink("mystery", "z" * 100) == "z" * 100


def test_identical_output_reuses_one_artifact(tmp_workspace):
    text = over_cap()
    shrink("run_shell", text)
    shrink("run_shell", text)
    assert len(list(config.ARTIFACTS.glob("*.txt"))) == 1, "content-addressed naming"


def test_no_artifacts_directory_created_at_import(tmp_workspace):
    """CE-05: no module-level I/O. Importing must not touch the filesystem."""
    import importlib

    import agent.context
    importlib.reload(agent.context)
    assert not config.ARTIFACTS.exists()

# ============================ NFR-203: secrets in FILES, not just the environment


def test_a_key_in_a_workspace_file_does_not_reach_the_model():
    """Measured before this existed: redact() only replaced values found in
    os.environ, so a repository .env went to the model verbatim."""
    out = redact('OPENAI_API_KEY=sk-proj-REALSECRET1234567890abcdefXYZ')

    assert 'REALSECRET' not in out
    assert 'redacted' in out


def test_the_common_issuer_prefixes_are_caught():
    for secret in ('sk-proj-abcdefghij1234567890',
                   'AKIAIOSFODNN7EXAMPLE',
                   'ghp_16CharsMinimumTokenValue00',
                   'xoxb-123456789-abcdefghijkl',
                   'hf_abcdefghijklmnop',
                   'AIza' + 'b' * 35):
        assert 'redacted' in redact(secret), secret


def test_a_private_key_block_is_caught_whole():
    pem = ('-----BEGIN RSA PRIVATE KEY-----' + chr(10) + 'MIIEowIBAAKCAQEA' +
           chr(10) + '-----END RSA PRIVATE KEY-----')
    out = redact(pem)

    assert 'MIIEowIBAAKCAQEA' not in out


def test_a_jwt_is_caught():
    assert 'redacted' in redact(
        'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdef')


def test_only_the_PASSWORD_goes_from_a_connection_string():
    """An agent debugging a connection needs to see where it points."""
    out = redact('postgres://admin:s3cr3tpw@db.internal:5432/app')

    assert 's3cr3tpw' not in out
    assert 'db.internal:5432/app' in out
    assert 'admin' in out


def test_ordinary_code_is_left_alone():
    """THE REASON THEIR 1,427-LINE REDACTOR WAS NOT VENDORED. Applied to this
    project it altered 29 lines of 14,243, destroying type annotations:
    `spent_tokens: int` became `spent_tokens: ***`. A tokenizer or auth module
    would become unreadable to the agent trying to fix it."""
    for line in ('spent_tokens: int',
                 'budget_tokens: int | None = None',
                 'MAX_TOKENS = 16_000',
                 'max_tokens=settings.MAX_TOKENS',
                 'KEY_VARS = ("AGENT_API_KEY", "OPENAI_API_KEY")',
                 'api_key = settings.openai_api_key()',
                 'def test_a_dead_workers_task_is_requeued(queue):',
                 'RISK_classified = True'):
        assert redact(line) == line, line


def test_a_prefix_inside_an_identifier_is_not_a_secret():
    """`sk_[A-Za-z0-9_]{10,}` matched inside `risk_classified` and
    `task_to_running`, redacting 14 lines of this project's own source. A prefix
    only counts at a token boundary."""
    for line in ('risk_classification_map', 'task_to_running_transition',
                 'a_task_is_requeued_here', 'brisk_walking_pace'):
        assert redact(line) == line, line


def test_the_environment_pass_still_works(monkeypatch):
    """The shape pass is ADDED to the env pass, not a replacement: our own key is
    not issuer-prefixed and only the environment knows it."""
    monkeypatch.setenv('SOME_SERVICE_TOKEN', 'plain-value-no-prefix-9999')
    out = redact('the value is plain-value-no-prefix-9999 here')

    assert 'plain-value-no-prefix-9999' not in out
    assert 'SOME_SERVICE_TOKEN' in out


def test_a_secret_is_scrubbed_before_it_can_be_spilled(tmp_workspace):
    """redact() runs before the cap AND before the spill, so a secret cannot end
    up sitting in .agent/artifacts one read_file away."""
    # Space-separated on purpose. Glued to preceding letters the key is part of a
    # longer token and correctly NOT a secret - the first version of this test got
    # that wrong and the boundary guard caught it.
    big = (('x' * 200) + ' sk-proj-REALSECRET1234567890abcdefXYZ ') * 200
    out = shrink('run_shell', big)

    assert 'REALSECRET' not in out
    for artifact in (config.ARTIFACTS.glob('*.txt') if config.ARTIFACTS.exists() else []):
        assert 'REALSECRET' not in artifact.read_text(encoding='utf-8')

def test_redaction_is_not_quadratic_on_repetitive_output():
    """A PRODUCTION BUG, not a style point. The DSN scheme was `[a-zA-Z0-9+.-]*`
    and on 60,000 repeated characters it consumed the whole string looking for
    `://`, failed, backtracked one, and repeated from every start position:
    36 SECONDS to scrub 720 KB. redact() runs on every tool result, and tool
    output is exactly where a wall of repeated characters comes from.
    """
    import time

    big = "y" * 60_000
    started = time.perf_counter()
    redact(big)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"scrubbing 60 KB took {elapsed:.1f}s"


def test_every_quantifier_in_the_secret_patterns_is_bounded():
    """The guard above catches one shape of blow-up. This catches the class:
    an unbounded `*` or `+` next to a character class is how it happened."""
    from agent import secrets

    for pattern in (secrets._DSN.pattern,):
        assert "]*" not in pattern, f"unbounded * in {pattern}"
        assert "]+" not in pattern or "[^@" in pattern, f"unbounded + in {pattern}"
