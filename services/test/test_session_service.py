"""
Summary / 文件概要:
Test the ten-minute stale browser-session takeover policy.
测试浏览器会话锁超过十分钟后的接管策略。
"""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from services import session_service


class SessionTakeoverTest(unittest.TestCase):
    """Verify active locks and stale recovery. / 验证活动锁及超时恢复。"""

    def setUp(self) -> None:
        with session_service._registry_lock:
            session_service._online_users.clear()

    def tearDown(self) -> None:
        with session_service._registry_lock:
            session_service._online_users.clear()

    def test_different_session_is_blocked_before_ten_minutes(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        session_service._online_users["alice"] = (
            session_service.SessionOwnership(
                session_id="old-browser",
                last_seen=now - timedelta(minutes=9),
            )
        )
        with patch.object(session_service, "_utc_now", return_value=now):
            acquired = session_service.acquire_user_session(
                "alice",
                "new-browser",
                last_history_activity=now - timedelta(minutes=20),
            )
        self.assertFalse(acquired)

    def test_stale_session_can_be_replaced(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        session_service._online_users["alice"] = (
            session_service.SessionOwnership(
                session_id="closed-browser",
                last_seen=now - timedelta(minutes=11),
            )
        )
        with patch.object(session_service, "_utc_now", return_value=now):
            acquired = session_service.acquire_user_session(
                "alice",
                "new-browser",
                last_history_activity=now - timedelta(minutes=15),
            )
        self.assertTrue(acquired)
        self.assertTrue(
            session_service.is_user_session_owner("alice", "new-browser")
        )

    def test_recent_history_keeps_owner_locked(self) -> None:
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        session_service._online_users["alice"] = (
            session_service.SessionOwnership(
                session_id="old-browser",
                last_seen=now - timedelta(minutes=20),
            )
        )
        with patch.object(session_service, "_utc_now", return_value=now):
            acquired = session_service.acquire_user_session(
                "alice",
                "new-browser",
                last_history_activity=now - timedelta(minutes=2),
            )
        self.assertFalse(acquired)


if __name__ == "__main__":
    unittest.main()
