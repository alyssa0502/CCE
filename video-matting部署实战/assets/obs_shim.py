#!/usr/bin/env python3
"""数据隔离垫片:模拟存储 API,把服务上传的结果落在节点本地目录,永不触网到客户域名。

背景(2026-11-04 铁律):video-matting 镜像内代码的 OSS_API_DOMAIN 默认值
指向客户生产 OSS。部署时必须:节点跑本垫片 + Deployment env 把
OSS_API_DOMAIN 钉到 http://<本机VPC IP>:<port>。之后结果落 RELAY_DIR,
再由自有桶转传脚本(V2 签名 PUT 到 obs-mcn)取走。客户 OSS 物理不可达。

接口(与真实 openapi 形状一致,服务端无感):
POST /app/storage/generateTempUploadKeyV2 -> {"code":0,"data":{"objectList":[{"url","name"}]}}
GET  /app/storage/presignV2?filepath=...   -> {"code":0,"data":"<download_url>"}
PUT  /upload/<name>                        -> 落盘 RELAY_DIR
GET  /download/<name>                      -> 读文件
GET  /ping                                 -> pong

环境变量:
  OBS_SHIM_PORT          监听端口,默认 8001
  OBS_SHIM_ADVERTISE_IP  本节点 VPC IP(拼进上传/下载 URL,必填)
  OBS_SHIM_RELAY_DIR     落盘目录,默认 /root/vm-build/obs_relay
  OBS_SHIM_PREFIX        对象前缀,默认 video-matting-test
节点上跑:OBS_SHIM_ADVERTISE_IP=<VPC IP> nohup python3 obs_shim.py >/root/shim.log 2>&1 &
"""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote, quote

PORT = int(os.environ.get("OBS_SHIM_PORT", "8001"))
NODE_VPC_IP = os.environ.get("OBS_SHIM_ADVERTISE_IP", "")
RELAY_DIR = os.environ.get("OBS_SHIM_RELAY_DIR", "/root/vm-build/obs_relay")
PREFIX = os.environ.get("OBS_SHIM_PREFIX", "video-matting-test")
os.makedirs(RELAY_DIR, exist_ok=True)


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        u = urlparse(self.path)
        print(f"[shim] GET {self.path[:120]}", flush=True)
        if u.path == "/ping":
            return self._send(200, b"pong", "text/plain")
        if u.path == "/app/storage/presignV2":
            q = parse_qs(u.query)
            filepath = (q.get("filepath") or [""])[0].lstrip("/")
            if not filepath:
                return self._json(400, {"code": 1, "message": "missing filepath"})
            dl = f"http://{NODE_VPC_IP}:{PORT}/download/{quote(filepath, safe='')}"
            return self._json(200, {"code": 0, "data": dl})
        if u.path.startswith("/download/"):
            name = os.path.basename(unquote(u.path[len("/download/"):]))
            fp = os.path.join(RELAY_DIR, name)
            if not os.path.isfile(fp):
                return self._send(404, b"not found", "text/plain")
            with open(fp, "rb") as f:
                return self._send(200, f.read(), "application/json")
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        print(f"[shim] POST {u.path} body={raw[:200]!r}", flush=True)
        if u.path == "/app/storage/generateTempUploadKeyV2":
            try:
                req = json.loads(raw or b"{}")
                files = req.get("files") or []
                obj_list = []
                for f in files:
                    key = os.path.basename(str(f.get("key") or "unnamed"))
                    name = f"{PREFIX}/{key}"
                    obj_list.append({
                        "url": f"http://{NODE_VPC_IP}:{PORT}/upload/{quote(name, safe='')}",
                        "name": name,
                    })
                return self._json(200, {"code": 0, "data": {"objectList": obj_list}})
            except Exception as e:
                return self._json(400, {"code": 1, "message": str(e)})
        return self._send(404, b"not found", "text/plain")

    def do_PUT(self):
        u = urlparse(self.path)
        print(f"[shim] PUT {u.path[:120]}", flush=True)
        if not u.path.startswith("/upload/"):
            return self._send(404, b"not found", "text/plain")
        name = os.path.basename(unquote(u.path[len("/upload/"):]))
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n) if n else b""
        fp = os.path.join(RELAY_DIR, name)
        with open(fp, "wb") as f:
            f.write(data)
        print(f"[shim] saved {fp} ({len(data)} bytes)", flush=True)
        return self._send(200, b'{"code":0}')

    def log_message(self, *a):
        pass  # 已自行打印


if __name__ == "__main__":
    if not NODE_VPC_IP:
        raise SystemExit("必须设置 OBS_SHIM_ADVERTISE_IP=<本节点VPC IP>")
    print(f"[shim] listening on 0.0.0.0:{PORT}, relay dir={RELAY_DIR}, "
          f"advertise={NODE_VPC_IP}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
