"""Single-page dashboard compatibility stub."""


def get_symbol_grid():
    return []


def get_bot_pulse(bot_state):
    return {"bot_state": bot_state}


def render_dashboard(bot_state):
    return {
        "symbol_grid": get_symbol_grid(),
        "bot_pulse": get_bot_pulse(bot_state),
        "bot_state": bot_state,
    }
