from runtime.kalshi_settlement_truth import build_weather_settlement_truth


def test_build_weather_settlement_truth_uses_broker_settlement_payout_math():
    truth = build_weather_settlement_truth(
        [
            {
                "ticker": "KXHIGHLAX-26JUN05-B69.5",
                "market_result": "yes",
                "yes_count_fp": "10.00",
                "no_count_fp": "0.00",
                "yes_total_cost_dollars": "6.20",
                "no_total_cost_dollars": "0.00",
                "fee_cost": "0.30",
            },
            {
                "ticker": "KXTEMPNYCH-26JUN0518-T75.99",
                "market_result": "no",
                "yes_count_fp": "0.00",
                "no_count_fp": "5.00",
                "yes_total_cost_dollars": "0.00",
                "no_total_cost_dollars": "3.10",
                "fee_cost": "0.15",
            },
        ],
        since_iso="2026-06-01",
    )

    assert truth["settlement_rows"] == 2
    assert truth["total"] == 2
    assert truth["wins"] == 2
    assert truth["losses"] == 0
    assert truth["total_pnl_usd"] == 5.25
    assert truth["by_bucket"]["Daily High"]["total_pnl_usd"] == 3.5
    assert truth["by_bucket"]["Hourly Temp"]["total_pnl_usd"] == 1.75
