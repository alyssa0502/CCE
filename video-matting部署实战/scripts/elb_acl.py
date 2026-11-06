#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给公网 ELB 的 TCP 监听器挂 IP 地址组白名单(仅放行指定公网 IP)。

2026-11-04 实测通过的流程与键名坑:
- ELB v3 API: GET /v3/{pid}/elb/loadbalancers 按 vip_address 或
  publicips[].publicip_address(注意:无下划线,不是 public_ip_address)匹配
- 白名单挂法: PUT /v3/{pid}/elb/listeners/{id},body 键是
  listener.ipgroup.ipgroup_id(不是 id),配 enable_ipgroup:true, type:"white"
- 共享型(union/vlb)ELB 的 TCP 监听器同样生效(外部非白名单 IP 超时,TCP 握手都不过)
- VPC 内访问 EIP 走内网直达 Pod ENI,不经过监听器——节点能访问不代表白名单失效

用法:
  python elb_acl.py --elb-ip 124.70.185.179 --allow-ip 111.119.234.6
  # 可选: --port 8001 --group-name vm-allow-user-pc --region cn-east-3
  #       --project-id <项目ID>
凭证只从环境变量 HWC_AK/HWC_SK 读(铁律:密钥字面量不进命令/文件/日志)。
"""
import argparse
import json
import os
import sys

from auth import sign_request  # 同目录,直接 import

import requests

AK = os.environ.get("HWC_AK", "")
SK = os.environ.get("HWC_SK", "")


def call(method, url, body=None):
    data = json.dumps(body) if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    h = sign_request(AK, SK, method, url, headers, data or "")
    r = requests.request(method, url, data=(data.encode() if data else None),
                         headers=h, timeout=30)
    if r.status_code >= 400:
        print(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
        sys.exit(1)
    return r.json()


def main():
    if not AK or not SK:
        sys.exit("HWC_AK/HWC_SK env not set")
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="cn-east-3")
    ap.add_argument("--project-id", default="ef13bf432f15434e92104390d62dc5bf",
                    help="cn-east-3(上海一)video-matting 项目;其他项目自行覆盖")
    ap.add_argument("--elb-ip", required=True, help="ELB 公网 EIP")
    ap.add_argument("--port", type=int, default=8001, help="监听器端口")
    ap.add_argument("--allow-ip", required=True, help="放行的公网 IP(用户出口)")
    ap.add_argument("--group-name", default="vm-allow-user-pc")
    args = ap.parse_args()

    base = f"https://elb.{args.region}.myhuaweicloud.com/v3/{args.project_id}/elb"
    pid = args.project_id

    # 1) 找 ELB(vip 或 publicips 双保险)
    lbs = call("GET", f"{base}/loadbalancers?limit=100").get("loadbalancers", [])
    lb = next((x for x in lbs if x.get("vip_address") == args.elb_ip), None)
    if not lb:
        for x in lbs:
            if args.elb_ip in [p.get("publicip_address", "")
                               for p in x.get("publicips", [])]:
                lb = x
                break
    if not lb:
        sys.exit(f"ELB {args.elb_ip} not found (total {len(lbs)} lbs)")
    print(f"LB: {lb['id']} {lb.get('name')} provider={lb.get('provider')}")

    # 2) 找端口对应的监听器
    lss = call("GET", f"{base}/listeners?loadbalancer_id={lb['id']}&limit=100") \
        .get("listeners", [])
    lsn = next((x for x in lss if x.get("protocol_port") == args.port), None)
    if not lsn:
        sys.exit(f"listener :{args.port} not found; "
                 f"ports={[x.get('protocol_port') for x in lss]}")

    # 3) 建/复用 IP 地址组(PUT 幂等刷新)
    groups = call("GET", f"{base}/ipgroups?limit=100").get("ipgroups", [])
    grp = next((g for g in groups if g.get("name") == args.group_name), None)
    body = {"ipgroup": {"name": args.group_name,
                        "ip_list": [{"ip": args.allow_ip, "description": "user-pc"}]}}
    if grp:
        grp = call("PUT", f"{base}/ipgroups/{grp['id']}", body).get("ipgroup")
    else:
        grp = call("POST", f"{base}/ipgroups", body).get("ipgroup")
    print(f"IPGroup: {grp['id']} ips={[i['ip'] for i in grp.get('ip_list', [])]}")

    # 4) 挂白名单并回读确认
    call("PUT", f"{base}/listeners/{lsn['id']}",
         {"listener": {"ipgroup": {"ipgroup_id": grp["id"],
                                    "enable_ipgroup": True, "type": "white"}}})
    back = call("GET", f"{base}/listeners/{lsn['id']}").get("listener", {})
    print("attached:", json.dumps(back.get("ipgroup"), ensure_ascii=False))
    print("DONE")


if __name__ == "__main__":
    main()
