#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华为云 IAM 认证模块。

功能：
  1. get_token        —— 获取 IAM Token（X-Subject-Token，24h 有效）
     - 优先尝试 AK/SK 方式（methods: ["hw_ak_sk"]，部分区域可能不支持）
     - 提供 --iam-user/--password 时改用密码方式（官方推荐，见 api_reference.md 1.1）
  2. sign_request     —— SDK-HMAC-SHA256 请求签名（AK/SK 直接调用 Open API，无需 Token）

用法：
  python auth.py --ak $AK --sk $SK --region cn-east-3 \
      --project-id $PID --domain-id $DID [--iam-user u --password p] \
      --output token.json

依赖：requests（pip install requests）
凭证只从命令行参数/环境变量读取，不落盘（token.json 仅保存 Token 本体）。
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import sys
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit("缺少依赖：pip install requests")


IAM_ENDPOINT = "iam.{region}.myhuaweicloud.com"


# ---------------------------------------------------------------------------
# SDK-HMAC-SHA256 签名（华为云 Open API 通用，详见 assets/api_reference.md 1.2）
# ---------------------------------------------------------------------------

def _url_encode_path(path: str) -> str:
    """CanonicalURI：对路径逐段 RFC3986 编码，保留 '/'。

    官方规则（api-sign-algorithm-001）：计算签名时 URI 必须以 '/' 结尾，
    发送请求时可以不以 '/' 结尾。此处统一补齐尾部斜杠。
    """
    encoded = urllib.parse.quote(path or "/", safe="/")
    if not encoded.endswith("/"):
        encoded += "/"
    return encoded


def _canonical_query(params: dict) -> str:
    """CanonicalQueryString：按 key 排序后 URL 编码拼接。"""
    items = []
    for k in sorted((params or {}).keys()):
        for v in (params[k] if isinstance(params[k], list) else [params[k]]):
            items.append(
                urllib.parse.quote(str(k), safe="") + "=" + urllib.parse.quote(str(v), safe="")
            )
    return "&".join(items)


def _canonical_request(method, parsed, headers, signed_names, payload) -> str:
    """按官方规则拼接 CanonicalRequest（供 sign_request 与自测复用）。"""
    canonical_headers = "".join(
        "{}:{}\n".format(name, next(v for k, v in headers.items() if k.lower() == name).strip())
        for name in signed_names
    )
    return "\n".join([
        method.upper(),
        _url_encode_path(parsed.path),
        _canonical_query(dict(urllib.parse.parse_qsl(parsed.query))),
        canonical_headers,
        ";".join(signed_names),
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    ])


def sign_request(ak: str, sk: str, method: str, url: str,
                 headers: dict = None, body: (str, bytes) = None) -> dict:
    """对请求做 SDK-HMAC-SHA256 签名，返回可直接使用的 headers dict。

    用法：
        headers = sign_request(AK, SK, "POST", url, {"Content-Type": "application/json"}, json_str)
        requests.post(url, data=json_str.encode(), headers=headers)
    """
    parsed = urllib.parse.urlsplit(url)
    host = parsed.netloc
    now = datetime.datetime.now(datetime.timezone.utc)
    sdk_date = now.strftime("%Y%m%dT%H%M%SZ")

    headers = dict(headers or {})
    headers.setdefault("Host", host)
    headers["X-Sdk-Date"] = sdk_date

    payload = ""
    if body is not None:
        payload = body if isinstance(body, str) else body.decode("utf-8")

    # 参与签名的 header：host / x-sdk-date / content-type（有 body 时）
    signed_names = ["host", "x-sdk-date"]
    if "content-type" in {k.lower() for k in headers}:
        signed_names.append("content-type")
    signed_names = sorted(signed_names)

    canonical_request = _canonical_request(method, parsed, headers, signed_names, payload)

    string_to_sign = "\n".join([
        "SDK-HMAC-SHA256",
        sdk_date,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signature = hmac.new(sk.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        "SDK-HMAC-SHA256 Access={}, SignedHeaders={}, Signature={}".format(
            ak, ";".join(signed_names), signature
        )
    )
    return headers


# ---------------------------------------------------------------------------
# Token 获取
# ---------------------------------------------------------------------------

def get_token_by_password(region, user, password, domain_name, project_id, domain_id=None):
    """密码方式获取 Token（官方标准方式，api_reference.md 1.1）。"""
    url = "https://{}/v3/auth/tokens".format(IAM_ENDPOINT.format(region=region))
    body = {
        "auth": {
            "identity": {
                "methods": ["password"],
                "password": {"user": {"name": user, "password": password,
                                      "domain": {"name": domain_name}}},
            },
            "scope": {"project": {"id": project_id}},
        }
    }
    r = requests.post(url, json=body, headers={"Content-Type": "application/json;charset=utf8"},
                      timeout=30)
    if r.status_code != 201:
        raise RuntimeError("密码方式获取 Token 失败 [{}]: {}".format(r.status_code, r.text[:500]))
    return r.headers.get("X-Subject-Token")


def get_token_by_aksk(region, ak, sk, project_id, domain_id=None):
    """AK/SK 方式获取 Token（methods: ["hw_ak_sk"]，部分区域可能不支持，见 1.3）。"""
    url = "https://{}/v3/auth/tokens".format(IAM_ENDPOINT.format(region=region))
    body = {
        "auth": {
            "identity": {
                "methods": ["hw_ak_sk"],
                "hw_ak_sk": {"access": ak, "secret": sk},
            },
            "scope": {"project": {"id": project_id}},
        }
    }
    r = requests.post(url, json=body, headers={"Content-Type": "application/json;charset=utf8"},
                      timeout=30)
    if r.status_code != 201:
        raise RuntimeError("AK/SK 方式获取 Token 失败 [{}]: {}".format(r.status_code, r.text[:500]))
    return r.headers.get("X-Subject-Token")


def main():
    ap = argparse.ArgumentParser(description="华为云 IAM 认证：获取 Token / 验证签名")
    ap.add_argument("--ak", default=os.environ.get("HWC_AK"))
    ap.add_argument("--sk", default=os.environ.get("HWC_SK"))
    ap.add_argument("--domain-id", default=os.environ.get("HWC_DOMAIN_ID"))
    ap.add_argument("--domain-name", default=os.environ.get("HWC_DOMAIN_NAME"))
    ap.add_argument("--iam-user", default=os.environ.get("HWC_IAM_USERNAME"))
    ap.add_argument("--password", default=os.environ.get("HWC_IAM_PASSWORD"))
    ap.add_argument("--region", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--output", default="token.json")
    args = ap.parse_args()

    token = None
    errors = []

    # 1) 密码方式（官方标准）
    if args.iam_user and args.password:
        try:
            domain_name = args.domain_name or args.iam_user.split("/")[0]
            token = get_token_by_password(args.region, args.iam_user, args.password,
                                          domain_name, args.project_id, args.domain_id)
            method = "password"
        except RuntimeError as e:
            errors.append(str(e))

    # 2) AK/SK 方式（降级尝试）
    if token is None and args.ak and args.sk:
        try:
            token = get_token_by_aksk(args.region, args.ak, args.sk, args.project_id)
            method = "hw_ak_sk"
        except RuntimeError as e:
            errors.append(str(e))

    # 3) 签名自检：AK/SK 签名调用 IAM 的项目列表接口，验证 AK/SK 本身有效
    if token is None and args.ak and args.sk:
        url = "https://{}/v3/auth/projects".format(IAM_ENDPOINT.format(region=args.region))
        try:
            h = sign_request(args.ak, args.sk, "GET", url)
            r = requests.get(url, headers=h, timeout=30)
            if r.status_code == 200:
                projects = {p["id"]: p["name"] for p in r.json().get("projects", [])}
                if args.project_id not in projects:
                    sys.exit("AK/SK 签名验证通过，但 project_id 不属于该账号。"
                             "可用项目：{}".format(projects))
                sys.exit("AK/SK 签名验证通过（可直接调用 Open API），但 Token 获取失败。\n"
                         "原因：{}\n解决：提供 --iam-user/--password 用密码方式获取 Token"
                         "（SWR 等接口需要），或让 deploy.py 全程使用 AK/SK 签名。".format(
                             "\n".join(errors)))
        except requests.RequestException as e:
            sys.exit("网络请求失败：{}".format(e))

    if token is None:
        sys.exit("获取 Token 失败：\n{}".format("\n".join(errors) or "未提供任何凭证"))

    result = {
        "token": token,
        "method": method,
        "region": args.region,
        "project_id": args.project_id,
        "domain_id": args.domain_id,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("Token 获取成功（方式：{}），已写入 {}。有效期 24 小时。".format(method, args.output))


if __name__ == "__main__":
    main()
