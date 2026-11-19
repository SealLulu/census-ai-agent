"""
Summary / 文件概要:
Maintain the single-instance in-memory online-user registry. Only session
ownership metadata is shared; chat history and pending records are never stored
in this module.
维护单实例进程内的在线用户表。这里只共享会话所有权信息，不保存聊天历史或
pending 记录。

Functions / 包含函数:
- generate_session_id: Generate a browser-session ID. / 生成浏览器会话 ID。
- acquire_user_session: Acquire ownership with stale-session recovery. / 获取登录权并恢复过期会话。
- release_user_session: Release login ownership on logout. / Logout 时释放登录权。
- is_user_session_owner: Check current ownership. / 检查当前会话是否持有登录权。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4


STALE_SESSION_TIMEOUT = timedelta(minutes=10)


@dataclass(frozen=True)
class SessionOwnership:
    """Record one in-memory owner and its last activity. / 记录内存会话持有者及最近活动。"""

    session_id: str
    last_seen: datetime


# Module state is shared by all Streamlit sessions in this single Python process.
# 模块状态由当前 Python 进程内的全部 Streamlit 会话共享。
_online_users: dict[str, SessionOwnership] = {}
_registry_lock = RLock()


def _utc_now() -> datetime:
    """Return an aware UTC timestamp. / 返回带时区的 UTC 时间。"""
    return datetime.now(timezone.utc)


def generate_session_id() -> str:
    """Generate a browser-session ID. / 生成浏览器会话 ID。"""
    return str(uuid4())


def acquire_user_session(
    username: str,
    session_id: str,
    last_history_activity: datetime | None = None,
) -> bool:
    """Acquire ownership or replace an owner inactive for ten minutes.

    获取登录权；若原持有者及最近历史均已超过十分钟无活动，则允许新会话接管。
    """
    with _registry_lock:
        now = _utc_now()
        owner = _online_users.get(username)
        if owner is not None and owner.session_id != session_id:
            activity_candidates = [owner.last_seen]
            if last_history_activity is not None:
                normalized_history_time = last_history_activity
                if normalized_history_time.tzinfo is None:
                    normalized_history_time = normalized_history_time.replace(
                        tzinfo=timezone.utc
                    )
                activity_candidates.append(
                    normalized_history_time.astimezone(timezone.utc)
                )
            latest_activity = max(activity_candidates)
            if now - latest_activity < STALE_SESSION_TIMEOUT:
                return False
        _online_users[username] = SessionOwnership(
            session_id=session_id,
            last_seen=now,
        )
        return True


def release_user_session(username: str, session_id: str) -> bool:
    """Release ownership only for its owner. / 仅允许持有者释放登录权。"""
    with _registry_lock:
        owner = _online_users.get(username)
        if owner is None or owner.session_id != session_id:
            return False
        del _online_users[username]
        return True


def is_user_session_owner(username: str, session_id: str) -> bool:
    """Check whether a session owns the login. / 检查会话是否持有该用户登录权。"""
    with _registry_lock:
        owner = _online_users.get(username)
        return owner is not None and owner.session_id == session_id
