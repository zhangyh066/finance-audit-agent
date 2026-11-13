import argparse
import getpass
import os
import sys

from finance_audit.db import connect, init_db
from finance_audit.demo_data import DEMO_PASSWORD, seed_demo


def main():
    parser = argparse.ArgumentParser(description="本地财务制度与单据智能审核工作台")
    parser.add_argument("--demo", action="store_true", help="首次启动时写入明确标注的合成样例数据")
    parser.add_argument("--init-only", action="store_true", help="只初始化，不启动服务")
    parser.add_argument("--admin-username", default="admin", help="非示例模式首次初始化的管理员账号")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if args.host != "127.0.0.1":
        raise ValueError("当前版本只允许监听本机回环地址")
    init_db()
    conn = connect()
    try:
        if args.demo:
            if seed_demo(conn):
                print("已初始化合成样例数据。请使用项目说明中的账号手动登录；不会自动登录。")
        elif not conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password = os.getenv("FINANCE_AUDIT_ADMIN_PASSWORD")
            if password is None:
                if not sys.stdin.isatty():
                    raise ValueError("首次初始化请通过终端设置管理员密码，或使用 FINANCE_AUDIT_ADMIN_PASSWORD 环境变量")
                password = getpass.getpass("设置本地管理员密码（至少 12 位，含字母和数字）：")
                if password != getpass.getpass("再次输入密码："):
                    raise ValueError("两次密码输入不一致")
            from finance_audit.bootstrap import initialize_workspace
            initialize_workspace(conn, args.admin_username, password)
            print(f"已创建本地管理员 {args.admin_username}，未写入任何合成业务数据。")
    finally:
        conn.close()
    print(f"本地工作台：http://127.0.0.1:{args.port}")
    if args.init_only:
        return
    import uvicorn
    from finance_audit.web import app
    uvicorn.run(app, host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    main()
