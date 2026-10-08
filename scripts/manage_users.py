"""管理员离线账号工具；默认预演，不从命令行参数接收密码。"""
import argparse
import getpass
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def manage_user(repo, username, role, *, password=None, disable=False):
    from sqlalchemy import select, update, insert, func
    from werkzeug.security import generate_password_hash
    if not username or len(username) > 128 or username != username.strip():
        raise ValueError("用户名不能为空、超过128字符或含首尾空格")
    if role not in {"reader", "analyst", "admin"}:
        raise ValueError("未知角色")
    if password is not None and not 12 <= len(password) <= 1024:
        raise ValueError("密码长度必须为12～1024字符")
    users = repo.schema.users
    with repo.engine.begin() as conn:
        # 串行化管理员调整，防止并发移除最后一个有效管理员。
        from sqlalchemy import text
        conn.execute(text("SELECT pg_advisory_xact_lock(410872013)"))
        current = conn.execute(select(users).where(users.c.username == username).with_for_update()).mappings().first()
        if current and current["active"] and current["role"] == "admin" and (disable or role != "admin"):
            admins = conn.execute(select(func.count()).select_from(users).where(users.c.role == "admin", users.c.active.is_(True))).scalar_one()
            if admins <= 1:
                raise ValueError("不能停用或降级最后一个有效管理员")
        if current is None and (disable or password is None):
            raise ValueError("新建账号必须提供密码，不能停用不存在的账号")
        values = {"role": role, "active": not disable}
        if password is not None:
            values["password_hash"] = generate_password_hash(password)
        uid = current["id"] if current else uuid4().hex
        if current:
            conn.execute(update(users).where(users.c.id == uid).values(**values))
        else:
            conn.execute(insert(users).values(id=uid, username=username, **values))
        conn.execute(insert(repo.schema.audit_logs).values(id=uuid4().hex, action="cli_user_update",
                     target=uid, payload={"role": role, "active": not disable, "password_changed": password is not None}))
        return uid


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("--role", choices=["reader", "analyst", "admin"], required=True)
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--set-password", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-database")
    args = parser.parse_args(argv)
    if not args.apply:
        print("仅预演：将创建/调整指定账号；未连接数据库、未读取密码。使用 --apply 与 --confirm-database 才执行。")
        return 0
    from sqlalchemy.engine import make_url
    from storage.repository import PostgresRepository
    url = os.environ.get("MIGRATION_DATABASE_URL", "")
    if not url or args.confirm_database != make_url(url).database:
        parser.error("需要迁移角色连接以及与目标数据库名一致的 --confirm-database")
    password = None
    if args.set_password:
        password = os.environ.get("NEW_USER_PASSWORD") or getpass.getpass("新密码（至少12字符，不回显）：")
    repo = PostgresRepository(url)
    try:
        manage_user(repo, args.username, args.role, password=password, disable=args.disable)
        print("账号及审计已保存；未输出密码。")
    finally:
        repo.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
