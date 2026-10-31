import os
import secrets
import subprocess
import sys


def test_production_deployment_checks_pass_with_secure_environment():
    environment = {
        **os.environ,
        "DEBUG": "0",
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "SECRET_KEY": secrets.token_urlsafe(64),
        "PROVIDER_WEBHOOK_SECRET": secrets.token_urlsafe(48),
        "VIRTUAL_ACCOUNT_SECRET": secrets.token_urlsafe(48),
        "ALLOWED_HOSTS": "ledger.example.com",
        "SECURE_SSL_REDIRECT": "1",
    }
    result = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_production_refuses_default_credentials_and_wildcard_hosts():
    environment = {**os.environ, "DEBUG": "0", "DJANGO_SETTINGS_MODULE": "config.settings", "ALLOWED_HOSTS": "*"}
    environment.pop("SECRET_KEY", None)
    result = subprocess.run(
        [sys.executable, "manage.py", "check"], env=environment, capture_output=True, text=True, timeout=60
    )
    assert result.returncode != 0
    assert "Production requires" in result.stderr
