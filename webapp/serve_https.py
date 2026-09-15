#!/usr/bin/env python3
"""启动 HTTPS 预览服务器（自签名证书）+ 每日维护线程。

用法:
    python3 serve_https.py                  # 127.0.0.1:9443, 启动维护线程（每天 05:00 HKT）
    python3 serve_https.py 9443             # 指定端口
    python3 serve_https.py 9443 0.0.0.0     # 指定端口 + 绑定地址
    python3 serve_https.py 9443 --no-maint  # 不启动维护线程
    python3 serve_https.py 9443 --check-now # 启动时立即检查一次新路线

浏览器访问: https://127.0.0.1:9443
自签名证书浏览器会告警，点"继续访问/高级→继续"即可。

维护线程：每天 05:00 (HKT) 检测两家公司的新路线，发现新路线则增量导入
其站点并重新导出 webapp/stops.json + stops_meta.json（前端轮询 meta 感知更新）。
"""
import http.server
import ssl
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent  # 项目根目录（maintenance.py / db.py 所在处）
CERT = HERE / "server.crt"
KEY = HERE / "server.key"
HKT = timezone(timedelta(hours=8))
MAINT_HOUR = 5  # 每天 HKT 05:00 检查

# 让维护线程能 import 项目根目录下的 maintenance / db 模块
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _next_run_target(now_hkt: datetime | None = None) -> datetime:
    """下一个 HKT 05:00 时刻。"""
    now = now_hkt or datetime.now(HKT)
    target = now.replace(hour=MAINT_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def _run_maintenance_safe(label: str) -> None:
    """执行一次维护，异常只打印不抛出（守护线程不能崩）。"""
    from maintenance import run_maintenance
    try:
        rep = run_maintenance()
        print(f"[maintenance:{label}] done: {rep}", flush=True)
    except Exception as e:
        print(f"[maintenance:{label}] error: {e}\n{traceback.format_exc()}", flush=True)


def _maintenance_loop() -> None:
    """后台线程：每天 05:00 (HKT) 检测新路线并更新数据。"""
    while True:
        target = _next_run_target()
        wait = (target - datetime.now(HKT)).total_seconds()
        print(f"[maintenance] next check at {target.isoformat()} (in {wait / 3600:.1f}h)", flush=True)
        time.sleep(wait)
        _run_maintenance_safe("daily")


def main() -> int:
    args = sys.argv[1:]
    no_maint = "--no-maint" in args
    check_now = "--check-now" in args
    args = [a for a in args if not a.startswith("--")]
    port = int(args[0]) if args else 9443
    host = args[1] if len(args) > 1 else "127.0.0.1"

    # 维护线程（webapp 服务自己定时，不依赖系统 crontab）
    if not no_maint:
        if check_now:
            threading.Thread(target=_run_maintenance_safe, args=("initial",), daemon=True).start()
        threading.Thread(target=_maintenance_loop, daemon=True).start()

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(CERT), str(KEY))
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(HERE), **kw)
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)

    print(f"HTTPS server: https://{host}:{port}  (self-signed cert)", flush=True)
    print(f"[maintenance] {'enabled: daily 05:00 HKT' + ('; initial check now' if check_now else '') if not no_maint else 'disabled'}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
