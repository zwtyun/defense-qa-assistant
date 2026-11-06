"""Windows launcher for local retrieval with selectable local/API generation."""
import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def health(port):
    try:
        with HTTP.open("http://127.0.0.1:%s/api/health" % port, timeout=0.5) as response:
            return json.loads(response.read())
    except Exception:
        return {}


def choose_port(preferred):
    for port in range(preferred, preferred + 20):
        info = health(port)
        if info.get("application") == "defense-search":
            return port, True
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
                return port, False
            except OSError:
                pass
    raise RuntimeError("本地端口被占用，请关闭旧实例后重试。")


def main():
    parser = argparse.ArgumentParser(description="答辩速查 2.2.2 · 现场答辩辅助")
    parser.add_argument("--headless", action="store_true", help="运行服务，不显示启动窗口")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    data_dir = (args.data_dir or Path(os.environ.get("DEFENSE_DATA_DIR", str(ROOT / "data")))).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DEFENSE_DATA_DIR"] = str(data_dir)
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(str(logs / "app.log"), encoding="utf-8")])
    port, existing = choose_port(args.port)
    url = "http://127.0.0.1:%s" % port
    if existing:
        if not args.no_browser:
            webbrowser.open(url)
        if sys.stdout:
            print("应用已运行：" + url)
        return

    import uvicorn
    from server import create_app
    app = create_app(data_dir=data_dir)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_config=None, access_log=False)
    service = uvicorn.Server(config)
    if args.headless:
        if not args.no_browser:
            threading.Timer(1.5, lambda: webbrowser.open(url)).start()
        if sys.stdout:
            print("答辩速查：" + url, flush=True)
        service.run()
        return

    import tkinter as tk
    from tkinter import messagebox
    window = tk.Tk()
    window.title("答辩速查 2.2.2 · 本地服务")
    window.geometry("460x260")
    window.resizable(False, False)
    window.configure(bg="#f4f6f3")
    tk.Label(window, text="答辩速查 2.2.2", font=("Microsoft YaHei UI", 21, "bold"),
             bg="#f4f6f3", fg="#173e32").pack(anchor="w", padx=25, pady=(23, 5))
    status = tk.StringVar(value="正在加载本地答案库，请稍候…")
    tk.Label(window, textvariable=status, font=("Microsoft YaHei UI", 10),
             bg="#f4f6f3", fg="#4f655c", wraplength=390, justify="left").pack(anchor="w", padx=27)
    tk.Label(window, text=url, font=("Consolas", 11), bg="#f4f6f3", fg="#173e32").pack(anchor="w", padx=27, pady=(9, 8))
    buttons = tk.Frame(window, bg="#f4f6f3")
    buttons.pack(fill="x", padx=25)
    open_button = tk.Button(buttons, text="打开检索界面", command=lambda: webbrowser.open(url),
                            bg="#1d5d47", fg="white", relief="flat", padx=18, pady=7,
                            font=("Microsoft YaHei UI", 10), state="disabled")
    open_button.pack(side="left")
    tk.Label(window, text="资料库保存在本机 · GPT API模式联网生成\n关闭此窗口将停止服务", bg="#f4f6f3",
             fg="#68776e", justify="left", font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=27, pady=(13, 0))
    failure = []
    ready = threading.Event()

    def run_server():
        try:
            service.run()
        except Exception as exc:
            logging.exception("服务启动失败")
            failure.append(str(exc))

    worker = threading.Thread(target=run_server, daemon=True)
    worker.start()

    def warmup():
        for _ in range(300):
            if health(port).get("application") == "defense-search":
                try:
                    with HTTP.open(url + "/api/search?q=%E6%8C%87%E6%A0%87", timeout=60) as response:
                        response.read()
                except Exception:
                    logging.exception("预热检索未完成")
                ready.set()
                return
            if not worker.is_alive():
                return
            time.sleep(0.2)

    threading.Thread(target=warmup, daemon=True).start()
    closing = [False]

    def check_ready():
        if closing[0]:
            return
        if ready.is_set():
            status.set("服务已就绪，答案检索无需联网。")
            open_button.configure(state="normal")
            if not args.no_browser:
                webbrowser.open(url)
        elif failure or not worker.is_alive():
            status.set("启动失败，请查看 logs/app.log。")
            messagebox.showerror("启动失败", failure[0] if failure else "本地服务未能启动，请查看日志。")
        else:
            window.after(200, check_ready)

    def stop():
        closing[0] = True
        status.set("正在停止本地服务…")
        open_button.configure(state="disabled")
        service.should_exit = True
        started = time.monotonic()

        def stopped():
            if not worker.is_alive() or time.monotonic() - started > 8:
                window.destroy()
            else:
                window.after(100, stopped)
        stopped()

    tk.Button(buttons, text="退出", command=stop, relief="flat", padx=15, pady=7,
              font=("Microsoft YaHei UI", 10), bg="#e4e9e3").pack(side="right")
    window.protocol("WM_DELETE_WINDOW", stop)
    window.after(150, check_ready)
    window.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        logging.exception("启动器出错")
        if getattr(sys, "frozen", False):
            import tkinter.messagebox
            tkinter.messagebox.showerror("答辩速查启动失败", str(error))
        else:
            raise
