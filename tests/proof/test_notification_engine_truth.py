from notifications.notification_engine import (
    CAT_KILL_SWITCH,
    CAT_SYSTEM,
    CAT_TRADE_OPEN,
    NotificationEvent,
    SEV_CRITICAL,
    SEV_INFO,
    SEV_WARNING,
    _render_telegram_message,
    _should_dispatch_telegram,
    notify_system,
)


def test_system_event_does_not_dispatch_telegram_even_if_legacy_flag_present():
    event = NotificationEvent(
        category=CAT_SYSTEM,
        severity=SEV_WARNING,
        title="Rate limit",
        message="Legacy noisy system event",
        data={"telegram": True},
    )

    assert _should_dispatch_telegram(event) is False


def test_order_filled_message_is_structured_and_dispatchable():
    event = NotificationEvent(
        category=CAT_TRADE_OPEN,
        severity=SEV_INFO,
        title="Filled",
        message="Entry filled",
        data={
            "telegram_event": "ORDER_FILLED",
            "symbol": "KXHIGHNY-30JUN26-T75",
            "direction": "YES",
            "contracts_filled": 7,
            "entry": 0.42,
            "capital_deployed": 3.43,
        },
    )

    rendered = _render_telegram_message(event)

    assert _should_dispatch_telegram(event) is True
    assert "ORDER_FILLED" in rendered
    assert "Contracts Filled: 7" in rendered
    assert "Total Capital Deployed: $3.43" in rendered


def test_kill_switch_resume_does_not_dispatch_critical_alert():
    event = NotificationEvent(
        category=CAT_KILL_SWITCH,
        severity=SEV_INFO,
        title="Kill switch resume",
        message="Recovered cleanly",
        data={"trigger": "runtime_loop_exception", "resume": True, "telegram_event": ""},
    )

    assert _should_dispatch_telegram(event) is False


def test_kill_switch_trigger_dispatches_critical_alert():
    event = NotificationEvent(
        category=CAT_KILL_SWITCH,
        severity=SEV_CRITICAL,
        title="Kill switch triggered",
        message="Execution cycle crashed",
        data={
            "trigger": "runtime_loop_exception",
            "resume": False,
            "telegram_event": "CRITICAL_KILL_SWITCH",
        },
    )

    assert _should_dispatch_telegram(event) is True
    assert "CRITICAL_KILL_SWITCH" in _render_telegram_message(event)


def test_notify_system_explicit_telegram_path_marks_operator_alert(monkeypatch):
    captured = {}

    def fake_push(event):
        captured["event"] = event
        return "row-id"

    monkeypatch.setattr("notifications.notification_engine.push", fake_push)

    notify_system(
        title="Kalshi API rate limit hit",
        detail="Execution controller paused entries.",
        severity=SEV_WARNING,
        telegram=True,
    )

    event = captured["event"]
    assert event.category == CAT_SYSTEM
    assert event.data["telegram"] is True
    assert event.data["telegram_event"] == "OPERATOR_ALERT"
    assert _should_dispatch_telegram(event) is True
    assert "OPERATOR ALERT" in _render_telegram_message(event)
