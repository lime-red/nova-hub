"""run.py --diff-template: what differs from config.toml.example (#126)."""

from pathlib import Path

from backend.core.config_template import UNSET, diff_against_template, format_diff

TEMPLATE = '''
[server]
host = "0.0.0.0"
data_dir = "./data"
# public_url = "https://hub.example.com"

[processing]
settle_seconds = 5
retention_days = 30

[dosemu]
timeout = 300
# term = "linux"

[security]
jwt_secret = "change-me-in-production"
cookie_secure = true

[alerting.webhook]
secret = ""
'''


def test_identical_is_clean():
    assert diff_against_template(TEMPLATE, TEMPLATE).clean


def test_per_host_keys_are_not_compared():
    config = TEMPLATE.replace('host = "0.0.0.0"', 'host = "127.0.0.1"') \
        .replace('data_dir = "./data"', 'data_dir = "/home/novahub/nova-data"\npublic_url = "https://x"') \
        .replace('"change-me-in-production"', '"s3cr3t"') \
        .replace('secret = ""', 'secret = "hook"')
    assert diff_against_template(config, TEMPLATE).clean


def test_a_changed_value_is_drift():
    config = TEMPLATE.replace("retention_days = 30", "retention_days = 7")
    d = diff_against_template(config, TEMPLATE)
    assert d.differs == [("processing.retention_days", 30, 7)]


def test_a_missing_key_is_left_to_the_code_default():
    config = TEMPLATE.replace("settle_seconds = 5\n", "")
    d = diff_against_template(config, TEMPLATE)
    assert d.unset == [("processing.settle_seconds", 5)]
    assert not d.differs


def test_setting_a_commented_key_is_a_difference():
    config = TEMPLATE.replace("timeout = 300", 'timeout = 300\nterm = "xterm"')
    d = diff_against_template(config, TEMPLATE)
    assert d.differs == [("dosemu.term", UNSET, "xterm")]
    assert not d.unknown


def test_a_key_the_template_does_not_know_is_reported():
    config = TEMPLATE.replace("retention_days = 30", "retention_days = 30\nretension_days = 9")
    d = diff_against_template(config, TEMPLATE)
    assert d.unknown == [("processing.retension_days", 9)]


def test_secret_values_are_never_printed():
    template = TEMPLATE + '\n[smtp]\npassword = "x"\n'
    config = template.replace('password = "x"', 'password = "hunter2-real"')
    d = diff_against_template(config, template)
    out = format_diff(d, "c", "t")
    assert "smtp.password" in out
    assert "hunter2-real" not in out


def test_the_shipped_example_is_clean_against_itself():
    text = (Path(__file__).parent.parent / "config.toml.example").read_text()
    assert diff_against_template(text, text).clean


def test_a_setting_about_passwords_is_not_hidden():
    template = "[security]\nmin_password_length = 12\n"
    config = template.replace("12", "16")
    out = format_diff(diff_against_template(config, template), "c", "t")
    assert "16" in out
