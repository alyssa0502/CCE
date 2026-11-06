#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""华为云 CCE Turbo 部署编排脚本（video-matting）。

子命令：
  create-network   创建 VPC / 子网 / 安全组（含 22、8001 入方向规则）
  create-cluster   创建 CCE Turbo 集群（category=Turbo + containerNetwork.mode=eni）
  wait-cluster     轮询集群状态直到 Available
  create-node      创建 GPU 节点（自动新购 EIP）
  wait-node        轮询节点状态直到 Active，输出公网 IP
  create-workload  获取集群证书 → 调 Kubernetes API 创建 Deployment + LoadBalancer Service
  swr-login        生成 SWR 临时 docker login 指令

用法示例：
  python deploy.py create-cluster --token-file token.json --cluster-name c1 \
      --vpc-id vpc-x --subnet-id subnet-x --output cluster.json

认证：--token-file（auth.py 产出）或 --ak/--sk/--region/--project-id（直接签名）。
所有 API 细节见 assets/api_reference.md。
"""
import argparse
import json
import os
import sys
import time
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit("缺少依赖：pip install requests")

from auth import sign_request  # noqa: E402  同目录导入


# ---------------------------------------------------------------------------
# 华为云 API 客户端（Token 或 AK/SK 签名，二选一）
# ---------------------------------------------------------------------------

class HwcClient:
    def __init__(self, region, project_id, token=None, ak=None, sk=None):
        if not token and not (ak and sk):
            raise ValueError("需要 --token-file 或 --ak/--sk")
        self.region = region
        self.project_id = project_id
        self.token = token
        self.ak, self.sk = ak, sk

    def request(self, method, service, path, body=None, params=None, timeout=60):
        url = "https://{}.{}.myhuaweicloud.com{}".format(service, self.region, path)
        if params:
            # 查询串必须参与签名：先按签名规则编码拼进 URL，再签名/发送
            query = "&".join(
                urllib.parse.quote(str(k), safe="") + "=" + urllib.parse.quote(str(v), safe="")
                for k, v in sorted(params.items()))
            url += "?" + query
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-Auth-Token"] = self.token
            r = requests.request(method, url, json=body, headers=headers, timeout=timeout)
        else:
            body_str = json.dumps(body) if body is not None else ""
            headers = sign_request(self.ak, self.sk, method, url, headers, body_str)
            r = requests.request(method, url, data=body_str.encode("utf-8") if body else None,
                                 headers=headers, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError("API 调用失败 [{} {}] HTTP {}:\n{}".format(
                method, path, r.status_code, r.text[:800]))
        return r.json() if r.content and r.headers.get("Content-Type", "").startswith("application/json") else {}


def load_client(args):
    """从 --token-file 或 --ak/--sk 构造客户端。"""
    if args.token_file:
        with open(args.token_file, encoding="utf-8") as f:
            t = json.load(f)
        return HwcClient(t["region"], t["project_id"], token=t["token"])
    return HwcClient(args.region, args.project_id, ak=args.ak, sk=args.sk)


def save(data, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("已写入 {}".format(path))


def poll(fn, ok, interval, max_wait, desc):
    """通用轮询：fn() 返回状态值，ok() 判断是否完成。"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        value = fn()
        if ok(value):
            return value
        print("  {} 当前状态：{}（{}s 后重试）".format(desc, value, interval))
        time.sleep(interval)
    raise TimeoutError("等待 {} 超时（{}s）".format(desc, max_wait))


# ---------------------------------------------------------------------------
# create-network
# ---------------------------------------------------------------------------

def cmd_create_network(args):
    c = load_client(args)
    pid = c.project_id
    result = {}

    if args.vpc_id and args.vpc_id != "auto_create":
        result["vpc_id"] = args.vpc_id
    else:
        print("创建 VPC ...")
        r = c.request("POST", "vpc", "/v1/{}/vpcs".format(pid),
                      {"vpc": {"name": args.vpc_name, "cidr": "192.168.0.0/16"}})
        result["vpc_id"] = r["vpc"]["id"]
        print("  VPC_ID = {}".format(result["vpc_id"]))

    if args.subnet_id and args.subnet_id != "auto_create":
        result["subnet_id"] = args.subnet_id
    else:
        print("创建子网 ...")
        r = c.request("POST", "vpc", "/v1/{}/subnets".format(pid), {"subnet": {
            "name": args.subnet_name, "cidr": "192.168.0.0/24", "gateway_ip": "192.168.0.1",
            "vpc_id": result["vpc_id"], "enable_dhcp": True,
            "primary_dns": "100.125.36.53", "secondary_dns": "100.125.5.151"}})
        result["subnet_id"] = r["subnet"]["id"]
        poll(lambda: c.request("GET", "vpc", "/v1/{}/subnets/{}".format(pid, result["subnet_id"]))
             .get("subnet", {}).get("status"),
             lambda s: s == "ACTIVE", 5, 120, "子网")
        print("  SUBNET_ID = {}".format(result["subnet_id"]))

    if args.sg_id and args.sg_id != "auto_create":
        result["security_group_id"] = args.sg_id
    else:
        print("创建安全组 ...")
        r = c.request("POST", "vpc", "/v1/{}/security-groups".format(pid),
                      {"security_group": {"name": args.sg_name, "vpc_id": result["vpc_id"]}})
        result["security_group_id"] = r["security_group"]["id"]
        for port in (22, args.service_port):
            c.request("POST", "vpc", "/v1/{}/security-group-rules".format(pid),
                      {"security_group_rule": {
                          "security_group_id": result["security_group_id"],
                          "direction": "ingress", "ethertype": "IPv4", "protocol": "tcp",
                          "port_range_min": port, "port_range_max": port}})
        print("  SECURITY_GROUP_ID = {}（已放通 22/{}）".format(result["security_group_id"], args.service_port))

    result["az"] = args.az
    save(result, args.output)


# ---------------------------------------------------------------------------
# create-cluster / wait-cluster
# ---------------------------------------------------------------------------

def cluster_body(args, vpc_id, subnet_id, sg_id, eni_subnet_id):
    """eni_subnet_id 必须传 neutron_subnet_id（IPv4 子网 ID），不是 v1 子网列表的 id！

    两者不同：v1 子网响应的 id = neutron_network_id（hostNetwork.subnet 用），
    neutron_subnet_id 字段才是 eniNetwork.subnets[].subnetID 要的值。
    """
    return {
        "kind": "Cluster", "apiVersion": "v3",
        "metadata": {"name": args.cluster_name},
        "spec": {
            "category": "Turbo",                 # CCE Turbo（云原生网络2.0）
            "type": "VirtualMachine",
            "flavor": args.cluster_flavor,
            "version": args.cluster_version,
            "hostNetwork": {"vpc": vpc_id, "subnet": subnet_id,
                            **({"securityGroup": sg_id} if sg_id else {})},
            "containerNetwork": {"mode": "eni"},  # Turbo 必用 eni
            "eniNetwork": {"subnets": [{"subnetID": eni_subnet_id or subnet_id}]},
            "serviceNetwork": {"IPv4CIDR": "10.247.0.0/16"},
            "authentication": {"mode": "rbac"},
            "billingMode": 0,
            "kubeProxyMode": "iptables",
        },
    }


def cmd_create_cluster(args):
    c = load_client(args)
    pid = c.project_id
    body = cluster_body(args, args.vpc_id, args.subnet_id, args.sg_id, args.eni_subnet_id)
    print("创建 CCE Turbo 集群 {} ...".format(args.cluster_name))
    r = c.request("POST", "cce", "/api/v3/projects/{}/clusters".format(pid), body)
    cluster_id = r["metadata"]["uid"]
    print("  CLUSTER_ID = {}（jobID={}）".format(cluster_id, r.get("status", {}).get("jobID")))
    save({"cluster_id": cluster_id, "cluster_name": args.cluster_name,
          "job_id": r.get("status", {}).get("jobID")}, args.output)


def cmd_wait_cluster(args):
    c = load_client(args)
    pid = c.project_id

    def get():
        r = c.request("GET", "cce", "/api/v3/projects/{}/clusters/{}".format(pid, args.cluster_id))
        return r

    phase = poll(lambda: get().get("status", {}).get("phase"),
                 lambda p: p == "Available", args.interval, args.max_wait, "集群")
    r = get()
    out = {
        "cluster_id": args.cluster_id,
        "phase": phase,
        "endpoints": r.get("status", {}).get("endpoints", {}),
        "version": r.get("spec", {}).get("version"),
    }
    print("集群已 Available。apiserver: {}".format(out["endpoints"].get("external")))
    save(out, args.output)


# ---------------------------------------------------------------------------
# create-node / wait-node
# ---------------------------------------------------------------------------

def cmd_create_node(args):
    c = load_client(args)
    pid = c.project_id
    body = {
        "kind": "Node", "apiVersion": "v3",
        "metadata": {"name": args.node_name},
        "spec": {
            "flavor": args.node_flavor,
            "az": args.az,                      # API 创建不支持 random
            "os": args.node_os,
            "login": {"sshKey": args.ssh_key},
            "rootVolume": {"size": int(args.root_volume_size), "volumetype": args.volume_type},
            "dataVolumes": [{"size": int(args.data_volume_size), "volumetype": args.volume_type}],
            "publicIP": {                       # 自动新购 EIP
                "count": int(args.node_count),
                "eip": {"iptype": "5_bgp", "bandwidth": {
                    "chargemode": "traffic", "name": args.node_name + "-eip",
                    "size": int(args.eip_bandwidth), "sharetype": "PER"}},
            },
            "count": int(args.node_count),
            "nodeNicSpec": {"primaryNic": {"subnetId": args.subnet_id}},
            "billingMode": 0,
        },
    }
    # 带驱动的节点镜像(复刻模式): GPU 驱动烧在节点镜像里,免装驱动
    if args.node_image_id:
        body["spec"]["extendParam"] = {"alpha.cce/NodeImageID": args.node_image_id}
    print("创建 {} 个 GPU 节点（{}）...".format(args.node_count, args.node_flavor))
    r = c.request("POST", "cce",
                  "/api/v3/projects/{}/clusters/{}/nodes".format(pid, args.cluster_id), body)
    node_id = r["metadata"]["uid"]
    print("  NODE_ID = {}".format(node_id))
    save({"node_id": node_id, "node_name": args.node_name,
          "job_id": r.get("status", {}).get("jobID")}, args.output)


def cmd_wait_node(args):
    c = load_client(args)
    pid = c.project_id

    def get():
        return c.request("GET", "cce", "/api/v3/projects/{}/clusters/{}/nodes/{}".format(
            pid, args.cluster_id, args.node_id))

    phase = poll(lambda: get().get("status", {}).get("phase"),
                 lambda p: p == "Active", args.interval, args.max_wait, "节点")
    r = get()
    out = {
        "node_id": args.node_id,
        "phase": phase,
        "public_ip": r.get("status", {}).get("publicIP"),
        "private_ip": r.get("status", {}).get("privateIP"),
        "server_id": r.get("status", {}).get("serverId"),
        "flavor": r.get("spec", {}).get("flavor"),
    }
    print("节点已 Active。公网 IP：{}（SSH 用户 root，密钥对登录）".format(out["public_ip"]))
    save(out, args.output)


# ---------------------------------------------------------------------------
# create-workload（证书 → Kubernetes API）
# ---------------------------------------------------------------------------

def _pick(d, *names):
    for n in names:
        for k in d:
            if k.lower() == n.lower():
                return d[k]
    raise KeyError("响应中找不到字段 {}，实际字段：{}".format(names, list(d)))


def cmd_create_workload(args):
    c = load_client(args)
    pid = c.project_id

    # 1) 获取集群证书（api_reference.md 3.5）
    print("获取集群证书 ...")
    cert = c.request("POST", "cce",
                     "/api/v3/projects/{}/clusters/{}/clustercert".format(pid, args.cluster_id),
                     {"duration": 1825})
    ca_pem = _pick(cert, "cacert", "ca")
    client_pem = _pick(cert, "clustercert", "clientCert", "cert")
    client_key = _pick(cert, "clientKey", "key")

    # 集群 apiserver 地址
    cluster = c.request("GET", "cce", "/api/v3/projects/{}/clusters/{}".format(pid, args.cluster_id))
    endpoint = cluster["status"]["endpoints"]["external"]  # https://EIP:5443
    print("apiserver: {}".format(endpoint))

    # 证书写入临时文件（requests 需要）
    workdir = os.path.dirname(os.path.abspath(args.output)) or "."
    ca_file = os.path.join(workdir, "cluster_ca.pem")
    cert_file = os.path.join(workdir, "client_cert.pem")
    key_file = os.path.join(workdir, "client_key.pem")
    for path, pem in ((ca_file, ca_pem), (cert_file, client_pem), (key_file, client_key)):
        with open(path, "w", encoding="utf-8") as f:
            f.write(pem if pem.endswith("\n") else pem + "\n")

    def k8s(method, path, body=None, ok_codes=(200, 201)):
        r = requests.request(method, endpoint + path, json=body,
                             cert=(cert_file, key_file), verify=ca_file, timeout=60)
        if r.status_code not in ok_codes:
            raise RuntimeError("K8s API [{} {}] HTTP {}:\n{}".format(
                method, path, r.status_code, r.text[:800]))
        return r.json() if r.content else {}

    # 2) 创建 Deployment（api_reference.md 4.1）
    name = args.workload_name
    image = args.image
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": name, "namespace": "default"},
        "spec": {
            "replicas": int(args.replicas),
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "containers": [{
                        "name": name,
                        "image": image,
                        "imagePullPolicy": "IfNotPresent",  # 本地镜像必配
                        "ports": [{"containerPort": int(args.service_port)}],
                        "resources": {"limits": {"nvidia.com/gpu": int(args.gpu_limit)}},
                        "env": [{"name": k, "value": v} for k, v in args.env],
                        "volumeMounts": [{"name": "dshm", "mountPath": "/dev/shm"}],
                    }],
                    "volumes": [{"name": "dshm",
                                 "emptyDir": {"medium": "Memory", "sizeLimit": args.shm_size}}],
                },
            },
        },
    }
    print("创建 Deployment {}/{}（image={}）...".format(name, image, image))
    k8s("POST", "/apis/apps/v1/namespaces/default/deployments", deployment)

    # 3) 创建 LoadBalancer Service（自动创建公网 ELB，api_reference.md 4.2）
    svc_name = args.service_name
    service = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {
            "name": svc_name, "namespace": "default",
            "annotations": {
                "kubernetes.io/elb.class": "union",
                "kubernetes.io/elb.autocreate": json.dumps({
                    "type": "public",
                    "bandwidth_name": svc_name + "-bw",
                    "bandwidth_size": int(args.elb_bandwidth),
                    "bandwidth_sharetype": "PER",
                    "bandwidth_chargemode": "traffic",
                    # CCE admission webhook 强制要求(type=public 时必填,缺了整个 Service 被拒)
                    "eip_type": "5_bgp",
                }),
            },
        },
        "spec": {
            "type": "LoadBalancer",
            "selector": {"app": name},
            "ports": [{"port": int(args.service_port), "targetPort": int(args.service_port)}],
        },
    }
    print("创建 Service {}（LoadBalancer，自动创建公网 ELB）...".format(svc_name))
    k8s("POST", "/api/v1/namespaces/default/services", service)

    # 4) 轮询 Pod Running
    def get_pods():
        r = requests.get(endpoint + "/api/v1/namespaces/default/pods",
                         params={"labelSelector": "app=" + name},
                         cert=(cert_file, key_file), verify=ca_file, timeout=60)
        return r.json().get("items", [])

    replicas = int(args.replicas)
    print("等待 Pod Running ...")
    phase = poll(
        lambda: ",".join(p["status"]["phase"] for p in get_pods()) or "<none>",
        lambda s: len(s.split(",")) == replicas and all(x == "Running" for x in s.split(",")),
        15, args.max_wait, "Pod")

    # 5) 获取 ELB 外部 IP
    svc_ip = poll(
        lambda: (k8s("GET", "/api/v1/namespaces/default/services/" + svc_name)
                 .get("status", {}).get("loadBalancer", {}).get("ingress", [{}])[0].get("ip", "")),
        lambda ip: bool(ip), 15, args.max_wait, "ELB IP")

    print("Pod Running，Service 外部地址：{}:{}".format(svc_ip, args.service_port))
    save({"workload": name, "service_name": svc_name, "service_ip": svc_ip,
          "service_port": args.service_port, "pod_phase": phase,
          "apiserver": endpoint}, args.output)
    # 清理证书临时文件（敏感）
    for p in (ca_file, cert_file, key_file):
        try:
            os.remove(p)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# swr-login
# ---------------------------------------------------------------------------

def cmd_swr_login(args):
    c = load_client(args)
    # manage API 独立域名是 swr-api.{region};swr.{region} 只是 docker registry 端点(2026-11-04 实测 404)
    url = "https://swr-api.{}.myhuaweicloud.com/v2/manage/utils/secret".format(c.region)
    headers = {"Content-Type": "application/json"}
    if c.token:
        headers["X-Auth-Token"] = c.token
        r = requests.post(url, params={"projectname": c.region}, headers=headers, timeout=30)
    else:
        body = ""
        headers = sign_request(c.ak, c.sk, "POST", url, headers, body)
        r = requests.post(url, params={"projectname": c.region}, headers=headers, timeout=30)
    if r.status_code != 200:
        raise RuntimeError("获取 SWR 登录指令失败 [{}]: {}".format(r.status_code, r.text[:500]))
    cmd = r.headers.get("X-Swr-Dockerlogin")
    print("在 GPU 节点上执行以下指令登录 SWR（过期时间 {}）：".format(
        r.headers.get("X-Swr-Expireat", "未知")))
    print(cmd)
    if args.output:
        save({"docker_login": cmd, "registry": "swr.{}.myhuaweicloud.com".format(c.region)},
             args.output)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_auth_args(ap):
    ap.add_argument("--token-file")
    ap.add_argument("--ak", default=os.environ.get("HWC_AK"))
    ap.add_argument("--sk", default=os.environ.get("HWC_SK"))
    ap.add_argument("--region")
    ap.add_argument("--project-id")


def main():
    ap = argparse.ArgumentParser(description="CCE Turbo 部署编排")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-network")
    add_auth_args(p)
    p.add_argument("--vpc-id", required=True)
    p.add_argument("--subnet-id", required=True)
    p.add_argument("--sg-id")
    p.add_argument("--eni-subnet-id")
    p.add_argument("--az", required=True)
    p.add_argument("--vpc-name", default="video-matting-vpc")
    p.add_argument("--subnet-name", default="video-matting-subnet")
    p.add_argument("--sg-name", default="video-matting-sg")
    p.add_argument("--service-port", default="8001")
    p.add_argument("--output", default="network.json")
    p.set_defaults(fn=cmd_create_network)

    p = sub.add_parser("create-cluster")
    add_auth_args(p)
    p.add_argument("--cluster-name", required=True)
    p.add_argument("--cluster-version", default="v1.36")
    p.add_argument("--cluster-flavor", default="cce.s2.small")
    p.add_argument("--vpc-id", required=True)
    p.add_argument("--subnet-id", required=True,
                   help="节点子网 network ID（v1 子网列表的 id）")
    p.add_argument("--sg-id")
    p.add_argument("--eni-subnet-id",
                   help="ENI 子网的 neutron_subnet_id（IPv4 子网ID，非 v1 列表 id！）")
    p.add_argument("--output", default="cluster.json")
    p.set_defaults(fn=cmd_create_cluster)

    p = sub.add_parser("wait-cluster")
    add_auth_args(p)
    p.add_argument("--cluster-id", required=True)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--max-wait", type=int, default=1500)
    p.add_argument("--output", default="cluster.json")
    p.set_defaults(fn=cmd_wait_cluster)

    p = sub.add_parser("create-node")
    add_auth_args(p)
    p.add_argument("--cluster-id", required=True)
    p.add_argument("--node-name", required=True)
    p.add_argument("--node-flavor", required=True)
    p.add_argument("--az", required=True)
    p.add_argument("--node-os", default="EulerOS 2.9")
    p.add_argument("--node-image-id", default=None,
                   help="带驱动的 CCE 节点镜像 ID(extendParam alpha.cce/NodeImageID);"
                        "cn-east-3 L2 用 8553a184-a9bb-4cf9-94cf-0177b9701766,"
                        "配 os='Huawei Cloud EulerOS 2.0'")
    p.add_argument("--root-volume-size", default="100")
    p.add_argument("--data-volume-size", default="200")
    p.add_argument("--volume-type", default="SAS")
    p.add_argument("--ssh-key", required=True)
    p.add_argument("--node-count", default="1")
    p.add_argument("--eip-bandwidth", default="100")
    p.add_argument("--subnet-id", required=True)
    p.add_argument("--output", default="node.json")
    p.set_defaults(fn=cmd_create_node)

    p = sub.add_parser("wait-node")
    add_auth_args(p)
    p.add_argument("--cluster-id", required=True)
    p.add_argument("--node-id", required=True)
    p.add_argument("--interval", type=int, default=30)
    p.add_argument("--max-wait", type=int, default=1200)
    p.add_argument("--output", default="node.json")
    p.set_defaults(fn=cmd_wait_node)

    p = sub.add_parser("create-workload")
    add_auth_args(p)
    p.add_argument("--cluster-id", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--workload-name", default="video-matting")
    p.add_argument("--service-name", default="video-matting-svc")
    p.add_argument("--service-port", default="8001")
    p.add_argument("--gpu-limit", default="1")
    p.add_argument("--shm-size", default="32Gi")
    p.add_argument("--replicas", default="1")
    p.add_argument("--elb-bandwidth", default="100")
    p.add_argument("--env", action="append", default=[], help="KEY=VALUE，可多次")
    p.add_argument("--max-wait", type=int, default=900)
    p.add_argument("--output", default="workload.json")
    p.set_defaults(fn=cmd_create_workload)

    p = sub.add_parser("swr-login")
    add_auth_args(p)
    p.add_argument("--output")
    p.set_defaults(fn=cmd_swr_login)

    args = ap.parse_args()
    if not args.token_file and not (args.ak and args.sk and args.region and args.project_id):
        sys.exit("认证参数不全：需要 --token-file，或 --ak/--sk/--region/--project-id")
    args.fn(args)


if __name__ == "__main__":
    main()
