"""Force-join settings and the access gate that enforces them."""

from __future__ import annotations

import logging
from typing import Optional

from linktofile.domain.models import ForceJoinPolicy
from linktofile.domain.ports import MembershipGateway, SettingsRepository

logger = logging.getLogger(__name__)

KEY_ENABLED = "force_join.enabled"
KEY_CHANNEL = "force_join.channel"
KEY_INVITE = "force_join.invite"


class SettingsService:
    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo
        self._policy: Optional[ForceJoinPolicy] = None

    def force_join(self) -> ForceJoinPolicy:
        if self._policy is None:
            self._policy = ForceJoinPolicy(
                enabled=self._repo.get(KEY_ENABLED, "0") == "1",
                channel=self._repo.get(KEY_CHANNEL, ""),
                invite_url=self._repo.get(KEY_INVITE, ""),
            )
        return self._policy

    def update_force_join(
        self,
        *,
        enabled: Optional[bool] = None,
        channel: Optional[str] = None,
        invite_url: Optional[str] = None,
    ) -> ForceJoinPolicy:
        current = self.force_join()
        policy = ForceJoinPolicy(
            enabled=current.enabled if enabled is None else enabled,
            channel=current.channel if channel is None else channel.strip(),
            invite_url=current.invite_url if invite_url is None else invite_url.strip(),
        )
        self._repo.set(KEY_ENABLED, "1" if policy.enabled else "0")
        self._repo.set(KEY_CHANNEL, policy.channel)
        self._repo.set(KEY_INVITE, policy.invite_url)
        self._policy = policy
        logger.info("Force-join updated: enabled=%s channel=%s", policy.enabled, policy.channel or "-")
        return policy

    def toggle_force_join(self) -> ForceJoinPolicy:
        return self.update_force_join(enabled=not self.force_join().enabled)


class AccessGuard:
    """Decides whether a user may use the bot right now.

    Fails open when Telegram cannot answer (an API outage must not lock
    everyone out), but a concrete "not a member" always blocks.
    """

    def __init__(self, settings: SettingsService, members: MembershipGateway) -> None:
        self._settings = settings
        self._members = members

    @property
    def policy(self) -> ForceJoinPolicy:
        return self._settings.force_join()

    async def allows(self, user_id: int) -> bool:
        policy = self.policy
        if not policy.is_active:
            return True
        return await self._members.is_member(user_id, policy.channel)

    def forget_membership_cache(self) -> None:
        self._members.forget()
