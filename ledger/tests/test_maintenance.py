from unittest.mock import patch

import pytest
from django.test import Client, override_settings


@pytest.mark.parametrize(
    "secret,header",
    [
        ("", "Bearer "),
        ("short", "Bearer short"),
        ("x" * 40, ""),
        ("x" * 40, "Bearer wrong"),
        ("x" * 40, "Bearer nonascii-\u00e9"),
    ],
)
def test_maintenance_refuses_missing_short_and_incorrect_secrets(secret, header):
    with override_settings(CRON_SECRET=secret), patch("ledger.tasks.publish_dispatches.delay") as publish:
        assert Client().get("/internal/maintenance/", HTTP_AUTHORIZATION=header).status_code == 401
        publish.assert_not_called()


def test_maintenance_queues_real_background_work_only_after_authentication():
    with (
        override_settings(CRON_SECRET="x" * 40),
        patch("ledger.tasks.publish_dispatches.delay") as dispatch,
        patch("ledger.tasks.expire_holds.delay") as expire,
        patch("ledger.tasks.publish_outbox.delay") as outbox,
        patch("ledger.tasks.nightly_reconciliation.delay") as reconcile,
    ):
        response = Client().get("/internal/maintenance/", HTTP_AUTHORIZATION=f"Bearer {'x' * 40}")
        assert response.status_code == 200
        for task in (dispatch, expire, outbox, reconcile):
            task.assert_called_once_with()


def test_maintenance_reports_queue_failure_without_exposing_exception():
    with (
        override_settings(CRON_SECRET="x" * 40),
        patch("ledger.tasks.publish_dispatches.delay", side_effect=RuntimeError("private broker credential")),
    ):
        response = Client().get("/internal/maintenance/", HTTP_AUTHORIZATION=f"Bearer {'x' * 40}")
        assert response.status_code == 503
        assert b"private" not in response.content
