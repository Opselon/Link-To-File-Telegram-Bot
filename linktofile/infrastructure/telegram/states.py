"""FSM states.

Only the dashboard needs a multi-step flow today, but the state machine is
typed so new flows (bulk mode, custom filename) can be added without string
keys scattered through the handlers.
"""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AdminFlow(StatesGroup):
    waiting_channel = State()
