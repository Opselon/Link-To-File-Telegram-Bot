"""Typed callback payloads (aiogram factories, 64-byte safe)."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class Nav(CallbackData, prefix="nav"):
    action: str  # home | back | close


class QualityCB(CallbackData, prefix="q"):
    kind: str  # video | audio
    value: str  # 360 | best | 192 ...
    ticket: int


class TicketCB(CallbackData, prefix="t"):
    action: str  # cancel | again | quality | close
    ticket: int


class AdminCB(CallbackData, prefix="adm"):
    action: str  # toggle | channel | cache | stats | menu


class JoinCB(CallbackData, prefix="join"):
    action: str  # check
