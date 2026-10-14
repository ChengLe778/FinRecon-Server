# -*- coding: utf-8 -*-
"""应收应付对账系统 · 双击启动入口（exe 与源码通用）

行为：
  1. 检查端口是否被占用
  2. 后台起 uvicorn
  3. 轮询到端口真正可连才打开浏览器（避免 Network Error）
  4. 前台阻塞，关闭窗口即停止服务
"""
import os
import socket
import sys
import threading
import time
import traceback
import webbrowser


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def _wait_and_open(port: int, max_wait: float = 60.0) -> None:
    """等服务真正能连上了再开浏览器"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            time.sleep(0.3)
            print("  服务已就绪，正在打开浏览器 ...")
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception:
                pass
            return
        time.sleep(0.4)
    print("  [警告] 服务启动超时，请手动打开浏览器访问 http://127.0.0.1:%d" % port)


def main() -> int:
    from app.main import app
    from app.config import HOST, PORT, DATA_DIR, BASE_DIR

    print("=" * 46)
    print("   应收应付对账系统（SQLite 版）")
    print("=" * 46)
    print(f"   数据目录 : {BASE_DIR}")
    print(f"   本机访问 : http://127.0.0.1:{PORT}")
    print("   局域网同事用 : http://<这台电脑的IP>:%d" % PORT)
    print()
    print("   使用期间请勿关闭本窗口，关闭即停止服务。")
    print("=" * 46)
    print()

    if not os.path.exists(str(DATA_DIR)):
        os.makedirs(str(DATA_DIR), exist_ok=True)

    if _port_open("127.0.0.1", PORT):
        print(f"[提示] 端口 {PORT} 已经被占用，可能服务已经在运行。")
        print(f"       直接访问 http://127.0.0.1:{PORT} 试试；")
        print("       若确认是别的程序占用，请先关闭它再重新双击。")
        print()
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception:
            pass
        input("按回车键退出 ...")
        return 1

    import uvicorn

    threading.Thread(target=_wait_and_open, args=(PORT,), daemon=True).start()
    try:
        uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
    except KeyboardInterrupt:
        print("\n服务已停止。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print()
        print("[错误] 服务启动失败，详细信息如下：")
        print("-" * 46)
        traceback.print_exc()
        print("-" * 46)
        try:
            input("\n按回车键退出 ...")
        except Exception:
            pass
        sys.exit(1)
