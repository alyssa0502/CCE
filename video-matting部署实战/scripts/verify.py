#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video-matting 服务验证脚本。

子命令：
  health-check    轮询 GET /health 直到 200
  warmup          POST /initialize 预热（超时至少 60s）
  test-inference  POST /video/segment_url 验证 NDJSON 推理流
  test-upload     POST /video/segment_stream 验证文件上传推理

用法：
  python verify.py health-check --host 1.2.3.4 --port 8001
依赖：requests
"""
import argparse
import json
import os
import sys
import time

try:
    import requests
except ImportError:
    sys.exit("缺少依赖：pip install requests")


def base_url(args):
    return "http://{}:{}".format(args.host, args.port)


def save(data, path):
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("已写入 {}".format(path))


# ---------------------------------------------------------------------------
# health-check
# ---------------------------------------------------------------------------

def cmd_health_check(args):
    url = base_url(args) + "/health"
    for i in range(1, args.max_retries + 1):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                body = r.json()
                print("[{}/{}] 健康检查通过：{}".format(i, args.max_retries, body))
                save({"health": "ok", "body": body}, args.output)
                return
            print("[{}/{}] HTTP {}（模型可能仍在加载）".format(i, args.max_retries, r.status_code))
        except requests.RequestException as e:
            print("[{}/{}] 连接失败：{}".format(i, args.max_retries, e.__class__.__name__))
        time.sleep(args.interval)
    sys.exit("健康检查连续 {} 次失败。建议查看 Pod 日志：kubectl logs -l app=video-matting".format(
        args.max_retries))


# ---------------------------------------------------------------------------
# warmup
# ---------------------------------------------------------------------------

def cmd_warmup(args):
    url = base_url(args) + "/initialize"
    print("预热中（离线加载 Florence-2 与 SAM2，最长 {}s）...".format(args.timeout))
    t0 = time.time()
    r = requests.post(url, timeout=args.timeout)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "warmed":
        sys.exit("预热返回异常：{}".format(body))
    print("预热完成：{}".format(body))
    save({"warmup": body, "elapsed_s": round(time.time() - t0, 1)}, args.output)


# ---------------------------------------------------------------------------
# NDJSON 流解析（推理/上传共用）
# ---------------------------------------------------------------------------

def check_ndjson(resp):
    """逐行解析 NDJSON，验证 status → meta → data → finish 流程。"""
    lines = [json.loads(l) for l in resp.text.splitlines() if l.strip()]
    keys = [next(iter(l)) for l in lines if isinstance(l, dict) and l]
    has = lambda k: any(k in l for l in lines)
    result = {
        "line_count": len(lines),
        "key_sequence": keys[:20],
        "has_status": has("status"),
        "has_meta": has("meta"),
        "data_count": sum(1 for l in lines if "data" in l),
        "has_finish": has("finish"),
        "finish": next((l["finish"] for l in lines if "finish" in l), None),
    }
    ok = result["has_status"] and result["has_meta"] and result["has_finish"]
    return ok, result


def cmd_test_inference(args):
    url = base_url(args) + "/video/segment_url"
    payload = {"video_url": args.video_url, "mode": args.mode}
    for k in ("oss_bucket", "oss_scene", "oss_download_expiration"):
        v = getattr(args, k)
        if v:
            payload[k] = int(v) if k == "oss_download_expiration" else v
    print("调用推理接口：{}".format(payload))
    r = requests.post(url, json=payload, timeout=args.timeout, stream=True)
    r.raise_for_status()
    ok, result = check_ndjson(r)
    print(("推理验证" if ok else "推理流不完整") + "：{}".format(result))
    save({"inference_ok": ok, "result": result}, args.output)
    if not ok:
        sys.exit(1)


def cmd_test_upload(args):
    url = base_url(args) + "/video/segment_stream"
    if not os.path.isfile(args.file):
        sys.exit("测试文件不存在：{}".format(args.file))
    print("上传 {} 调用推理接口 ...".format(args.file))
    with open(args.file, "rb") as f:
        r = requests.post(url, files={"file": f},
                          data={"mode": args.mode}, timeout=args.timeout, stream=True)
    r.raise_for_status()
    ok, result = check_ndjson(r)
    print(("上传推理验证" if ok else "推理流不完整") + "：{}".format(result))
    save({"upload_ok": ok, "result": result}, args.output)
    if not ok:
        sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="video-matting 服务验证")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, out):
        p.add_argument("--host", required=True)
        p.add_argument("--port", default="8001")
        p.add_argument("--output", default=out)
        return p

    p = common(sub.add_parser("health-check"), "verify_result.json")
    p.add_argument("--max-retries", type=int, default=30)
    p.add_argument("--interval", type=int, default=10)
    p.set_defaults(fn=cmd_health_check)

    p = common(sub.add_parser("warmup"), "verify_result.json")
    p.add_argument("--timeout", type=int, default=90)
    p.set_defaults(fn=cmd_warmup)

    p = common(sub.add_parser("test-inference"), "verify_result.json")
    p.add_argument("--video-url", required=True)
    p.add_argument("--mode", default="auto")
    p.add_argument("--oss-bucket")
    p.add_argument("--oss-scene")
    p.add_argument("--oss-download-expiration")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(fn=cmd_test_inference)

    p = common(sub.add_parser("test-upload"), "verify_result.json")
    p.add_argument("--file", required=True)
    p.add_argument("--mode", default="auto")
    p.add_argument("--timeout", type=int, default=600)
    p.set_defaults(fn=cmd_test_upload)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
