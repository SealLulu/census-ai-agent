"""
Summary / 文件概要:
Maintain the single-instance in-memory online-user registry. Only session
ownership metadata is shared; chat history and pending records are never stored
in this module.
维护单实例进程内的在线用户表。这里只共享会话所有权信息，不保存聊天历史或
pending 记录。

Functions / 包含函数:
- generate_session_id: Generate a browser-session ID. / 生成浏览器会话 ID。
- acquire_user_session: Acquire exclusive login ownership. / 获取用户独占登录权。
- release_user_session: Release login ownership on logout. / Logout 时释放登录权。
- is_user_session_owner: Check current ownership. / 检查当前会话是否持有登录权。
"""

from threading import RLock
from uuid import uuid4


# Module state is shared by all Streamlit sessions in this single Python process.
# 模块状态由当前 Python 进程内的全部 Streamlit 会话共享。
_online_users: dict[str, str] = {}
_registry_lock = RLock()


def generate_session_id() -> str:
    """Generate a browser-session ID. / 生成浏览器会话 ID。"""
    return str(uuid4())


def acquire_user_session(username: str, session_id: str) -> bool:
    """Acquire exclusive login ownership. / 获取用户的独占登录权。"""
    with _registry_lock:
        owner = _online_users.get(username)
        if owner is not None and owner != session_id:
            return False
        _online_users[username] = session_id
        return True


def release_user_session(username: str, session_id: str) -> bool:
    """Release ownership only for its owner. / 仅允许持有者释放登录权。"""
    with _registry_lock:
        if _online_users.get(username) != session_id:
            return False
        del _online_users[username]
        return True


def is_user_session_owner(username: str, session_id: str) -> bool:
    """Check whether a session owns the login. / 检查会话是否持有该用户登录权。"""
    with _registry_lock:
        return _online_users.get(username) == session_id
