# 华为云 API 参考清单（video-matting CCE Turbo 部署）

本文档覆盖部署全流程涉及的华为云 API 与服务自身接口。所有 `{region}` 替换为区域代码（如 `cn-east-3`），`{project_id}` 替换为项目 ID。

## 目录

1. [认证与签名](#1-认证与签名)
2. [VPC 网络类 API](#2-vpc-网络类-api)
3. [CCE 集群与节点 API](#3-cce-集群与节点-api)
4. [Kubernetes 工作负载 API](#4-kubernetes-工作负载-api)
5. [SWR 镜像仓库 API](#5-swr-镜像仓库-api)
6. [ECS 辅助 API](#6-ecs-辅助-api)
7. [video-matting 服务自身接口](#7-video-matting-服务自身接口)
8. [常用错误码](#8-常用错误码)

---

## 1. 认证与签名

### 1.1 获取 IAM 用户 Token（密码方式）

```
POST https://iam.{region}.myhuaweicloud.com/v3/auth/tokens
Content-Type: application/json;charset=utf8
```

请求体：

```json
{
  "auth": {
    "identity": {
      "methods": ["password"],
      "password": {
        "user": {
          "name": "{IAM用户名}",
          "password": "{IAM用户密码}",
          "domain": { "name": "{账号名}" }
        }
      }
    },
    "scope": { "project": { "id": "{project_id}" } }
  }
}
```

- 成功返回 **201**，Token 在响应头 **`X-Subject-Token`** 中（不是响应体）。
- Token 有效期 **24 小时**。修改密码/AK/权限后 Token 最长 30 分钟失效。
- `scope` 二选一：`project`（项目级，用于 CCE/VPC/ECS 等项目级服务，本部署用这种）或 `domain`（全局级）。
- 官方文档：https://support.huaweicloud.com/api-iam/iam_30_0001.html

### 1.2 AK/SK 签名请求（SDK-HMAC-SHA256）

所有华为云 Open API 均可直接用 AK/SK 签名调用，无需 Token。签名算法：

```
CanonicalRequest =
    HTTPRequestMethod + "\n" +
    CanonicalURI + "\n" +            // URL 编码后的路径，保留 "/"；★必须以 "/" 结尾（发送请求时可不带）
    CanonicalQueryString + "\n" +    // 按 key 排序、URL 编码的 k=v&k=v
    CanonicalHeaders + "\n" +        // 小写 header 名:trim 值\n，按名排序；末行也带 \n，
                                     // 叠加拼接公式的 \n，与 SignedHeaders 之间有一个空行
    SignedHeaders + "\n" +           // 参与签名的小写 header 名，分号分隔；x-sdk-date 必须参与
    HexEncode(SHA256(RequestPayload))  // 无 body 时对 "" 取哈希

StringToSign =
    "SDK-HMAC-SHA256" + "\n" +
    X-Sdk-Date值 + "\n" +            // 格式 YYYYMMDDTHHMMSSZ（UTC），与请求头一致，时差>15分钟被拒
    HexEncode(SHA256(CanonicalRequest))

Signature = HexEncode(HMAC-SHA256(SK, StringToSign))
```

最终请求头：

```
X-Sdk-Date: 20261031T120000Z
Authorization: SDK-HMAC-SHA256 Access={AK}, SignedHeaders=content-type;host;x-sdk-date, Signature={signature}
```

要点：
- **CanonicalURI 必须以 `/` 结尾**（如请求 `/v3/auth/projects`，签名时用 `/v3/auth/projects/`）——漏掉斜杠是签名 401 的最常见原因。
- `host` 与 `x-sdk-date` 必须参与签名；有 body 时 `content-type` 也必须参与。
- 本技能 `scripts/auth.py` 已实现该算法（`sign_request()`），`deploy.py` 所有调用复用。
- 官方签名指南（含完整示例）：https://support.huaweicloud.com/devg-apisign/api-sign-algorithm-001.html 、https://support.huaweicloud.com/devg-apisign/api-sign-algorithm-002.html

### 1.3 AK/SK 换 Token（可选）

`POST /v3/auth/tokens` 支持 `methods: ["hw_ak_sk"]`，请求体：

```json
{
  "auth": {
    "identity": {
      "methods": ["hw_ak_sk"],
      "hw_ak_sk": { "access": "{AK}", "secret": "{SK}" }
    },
    "scope": { "project": { "id": "{project_id}" } }
  }
}
```

> 注意：该方式未列入官方 Token 管理 API 列表，部分区域/账号可能返回 400。若失败，改用 1.1 密码方式或 1.2 直接签名。`scripts/auth.py` 会自动降级并给出明确提示。

---

## 2. VPC 网络类 API

Base URL：`https://vpc.{region}.myhuaweicloud.com`，认证方式：Token（`X-Auth-Token`）或 AK/SK 签名均可。

### 2.1 创建 VPC

```
POST /v1/{project_id}/vpcs
```

```json
{ "vpc": { "name": "video-matting-vpc", "cidr": "192.168.0.0/16", "description": "auto created by cce-deploy skill" } }
```

响应 `200`：`{"vpc": {"id": "vpc-id", "name": "...", "cidr": "...", "status": "OK"}}`

### 2.2 创建子网

```
POST /v1/{project_id}/subnets
```

```json
{
  "subnet": {
    "name": "video-matting-subnet",
    "cidr": "192.168.0.0/24",
    "gateway_ip": "192.168.0.1",
    "vpc_id": "{vpc_id}",
    "enable_dhcp": true,
    "primary_dns": "100.125.36.53",
    "secondary_dns": "100.125.5.151"
  }
}
```

响应 `200`：`{"subnet": {"id": "subnet-id", ...}}`

> **重要**：VPC v1 子网响应中 `id` = `neutron_network_id`（给 CCE `hostNetwork.subnet` 用）；`neutron_subnet_id` 是另一个 ID（给 CCE Turbo `eniNetwork.subnets[].subnetID` 用）。**两个 ID 不相同，用错场景会被 CCE 拒绝**。子网创建为异步，需轮询 `GET /v1/{project_id}/subnets/{subnet_id}` 直到 `status` 为 `ACTIVE`。

### 2.3 创建安全组

```
POST /v1/{project_id}/security-groups
```

```json
{ "security_group": { "name": "video-matting-sg", "vpc_id": "{vpc_id}" } }
```

响应 `200`：`{"security_group": {"id": "sg-id", "name": "...", "security_group_rules": [...]}}`

### 2.4 添加安全组规则

```
POST /v1/{project_id}/security-group-rules
```

放通 SSH（22）与服务端口（8001）入方向，各调一次：

```json
{
  "security_group_rule": {
    "security_group_id": "{sg_id}",
    "direction": "ingress",
    "ethertype": "IPv4",
    "protocol": "tcp",
    "port_range_min": 8001,
    "port_range_max": 8001
  }
}
```

> CCE 节点实际会使用集群自动创建的安全组；此处创建的 `sg_id` 建议同时传入集群创建的 `hostNetwork.securityGroup`（可选），并至少保证 VPC 内 8001/22 可达。

### 2.5 查询子网状态（轮询用）

```
GET /v1/{project_id}/subnets/{subnet_id}
```

`status` 取值：`ACTIVE`（正常）/ `PENDING`（创建中）/ `ERROR`。

---

## 3. CCE 集群与节点 API

Base URL：`https://cce.{region}.myhuaweicloud.com`，认证方式：Token 或 AK/SK 签名。

### 3.1 创建集群（CCE Turbo）

```
POST /api/v3/projects/{project_id}/clusters
```

请求体（**Turbo 集群关键字段组合**：`category: "Turbo"` + `containerNetwork.mode: "eni"` + `eniNetwork.subnets`）：

```json
{
  "kind": "Cluster",
  "apiVersion": "v3",
  "metadata": { "name": "video-matting-cluster" },
  "spec": {
    "category": "Turbo",
    "type": "VirtualMachine",
    "flavor": "cce.s2.small",
    "version": "v1.36",
    "hostNetwork": {
      "vpc": "{vpc_id}",
      "subnet": "{subnet_id}",
      "securityGroup": "{sg_id}"
    },
    "containerNetwork": { "mode": "eni" },
    "eniNetwork": { "subnets": [ { "subnetID": "{eni_subnet_id}" } ] },
    "serviceNetwork": { "IPv4CIDR": "10.247.0.0/16" },
    "authentication": { "mode": "rbac" },
    "billingMode": 0,
    "kubeProxyMode": "iptables"
  }
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `spec.category` | `"CCE"`（普通集群）或 `"Turbo"`（Turbo 集群）；eni 模式下默认 Turbo |
| `spec.type` | `"VirtualMachine"`（x86）/ `"ARM64"`（鲲鹏） |
| `spec.flavor` | `cce.s1.small`（单控制节点，≤50 节点）/ `cce.s2.small|medium|large|xlarge`（三控制节点高可用） |
| `spec.version` | 如 `v1.36`，不填默认最新 |
| `spec.hostNetwork` | **必填**。`vpc`=VPC ID，`subnet`=子网网络 ID（neutron_network_id） |
| `spec.containerNetwork.mode` | `overlay_l2`（隧道）/ `vpc-router`（VPC 网络）/ **`eni`（云原生网络 2.0，Turbo 必用）** |
| `spec.eniNetwork.subnets` | **Turbo 必填**，元素结构 `{"subnetID": "..."}`，填 **`neutron_subnet_id`（IPv4 子网 ID）**——不是 v1 子网列表返回的 `id`（那是 network ID）。填错报 `CCE.01400001 Eni subnetId is not in cluster vpc`。建议与节点子网分开 |
| `spec.serviceNetwork.IPv4CIDR` | Service 网段，默认 `10.247.0.0/16`，不能与 VPC 网段冲突 |
| `spec.billingMode` | 0=按需，1=包周期 |

响应 `201`：`metadata.uid`（即 cluster_id）、`status.phase`（初始 `Creating`）、`status.jobID`。

> 前置条件：账号需已配置 CCE 委托（`cce_admin_trust`）；每区域默认最多 5 个集群。
> 官方文档：https://support.huaweicloud.com/api-cce/cce_02_0236.html

### 3.2 查询集群详情（轮询用）

```
GET /api/v3/projects/{project_id}/clusters/{cluster_id}
```

- `status.phase`：`Creating` → **`Available`**（可用）/ `Deleting` / `Error`，一般 5~15 分钟。
- `status.endpoints.external`：如 `"https://EIP:5443"`，访问 apiserver 用。
- `metadata.uid`：集群 ID。

### 3.3 创建节点

```
POST /api/v3/projects/{project_id}/clusters/{cluster_id}/nodes
```

请求体：

```json
{
  "kind": "Node",
  "apiVersion": "v3",
  "metadata": { "name": "video-matting-node-01" },
  "spec": {
    "flavor": "pi5.4xlarge.4",
    "az": "cn-east-3a",
    "os": "EulerOS 2.9",
    "login": { "sshKey": "{SSH密钥对名称}" },
    "rootVolume": { "size": 100, "volumetype": "SAS" },
    "dataVolumes": [ { "size": 200, "volumetype": "SAS" } ],
    "publicIP": {
      "count": 1,
      "eip": {
        "iptype": "5_bgp",
        "bandwidth": { "chargemode": "traffic", "name": "video-matting-eip", "size": 100, "sharetype": "PER" }
      }
    },
    "count": 1,
    "nodeNicSpec": { "primaryNic": { "subnetId": "{subnet_id}" } },
    "billingMode": 0
  }
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `spec.flavor` | GPU 规格如 `pi5.4xlarge.4`（实测 1×L2 24G，AZ cn-east-3d）；需先确认区域/可用区支持——盘点法：ECS `GET /v2.1/{pid}/flavors/detail?limit=1000` marker 翻页 + `os-extra_specs` 读 `info:gpus`/`cond:operation:az`（注意 v2.1 默认只回 1000 条，不翻页会漏） |
| `spec.az` | **必填**，API 创建不支持 `random` |
| `spec.os` | 如 `EulerOS 2.9`、`Huawei Cloud EulerOS 2.0`；GPU 节点建议 EulerOS 2.9 |
| `spec.login` | `sshKey`（密钥对名，需预先在控制台创建）或 `userPassword` |
| `spec.rootVolume.size` | 40~1024 GB；`volumetype`：SAS/SSD/SATA/ESSD/GPSSD 等 |
| `spec.publicIP` | `ids`（已有 EIP）或 `count`+`eip`（自动新购 EIP） |
| `spec.count` | 创建节点时必填，≥1 |

响应 `201`：`metadata.uid`（node_id）、`status.jobID`。

> 官方文档：https://support.huaweicloud.com/api-cce/cce_02_0242.html

### 3.4 查询节点详情（轮询用）

```
GET /api/v3/projects/{project_id}/clusters/{cluster_id}/nodes/{node_id}
```

`status` 关键字段：

| 字段 | 说明 |
|------|------|
| `status.phase` | `Build`/`Installing` → **`Active`**（运行中）/ `Abnormal` / `Error`，一般 5~10 分钟 |
| `status.publicIP` | 节点弹性公网 IP（SSH 构建镜像用） |
| `status.privateIP` | 节点私网 IP |
| `status.serverId` | 底层 ECS 云服务器 ID |

### 3.5 获取集群证书（访问 Kubernetes API 前置）

```
POST /api/v3/projects/{project_id}/clusters/{cluster_id}/clustercert
Content-Type: application/json
```

请求体：`{"duration": 1825}`（有效天数，默认/最大 1825）

响应 `200`：

```json
{
  "clustercert": "-----BEGIN CERTIFICATE-----\n...",   // 客户端证书
  "cacert": "-----BEGIN CERTIFICATE-----\n...",        // CA 证书
  "clientKey": "-----BEGIN RSA PRIVATE KEY-----\n..."  // 客户端私钥
}
```

> 字段名以实际响应为准（部分版本为 `clientCert`/`clientKey`/`caCert` 大小写变体，脚本已做兼容）。证书用于以客户端证书方式调用集群 apiserver 的原生 Kubernetes API。
> 官方文档：https://support.huaweicloud.com/api-cce/cce_02_0248.html

---

## 4. Kubernetes 工作负载 API

CCE 没有独立的 Deployment REST 接口；工作负载通过**集群原生 Kubernetes API**创建。两种方式：

- **方式 A（本技能采用）**：3.5 获取证书后，用客户端证书直接调 apiserver（`https://{external_endpoint}`，即集群详情 `status.endpoints.external` 的地址）。
- **方式 B**：用 3.5 的证书生成 kubeconfig，`kubectl apply -f deployment.yaml`。

### 4.1 创建 Deployment

```
POST https://{apiserver}/apis/apps/v1/namespaces/default/deployments
Content-Type: application/json
（客户端证书认证：cert=clustercert, key=clientKey, verify=cacert）
```

请求体（video-matting GPU 工作负载）：

```json
{
  "apiVersion": "apps/v1",
  "kind": "Deployment",
  "metadata": { "name": "video-matting", "namespace": "default" },
  "spec": {
    "replicas": 1,
    "selector": { "matchLabels": { "app": "video-matting" } },
    "template": {
      "metadata": { "labels": { "app": "video-matting" } },
      "spec": {
        "containers": [
          {
            "name": "video-matting",
            "image": "video-matting:local",
            "ports": [ { "containerPort": 8001 } ],
            "resources": { "limits": { "nvidia.com/gpu": 1 } },
            "env": [
              { "name": "PORT", "value": "8001" },
              { "name": "GPU_CONCURRENCY_LIMIT", "value": "1" },
              { "name": "SAM2_COMPILE", "value": "false" }
            ],
            "volumeMounts": [ { "name": "dshm", "mountPath": "/dev/shm" } ]
          }
        ],
        "volumes": [
          { "name": "dshm", "emptyDir": { "medium": "Memory", "sizeLimit": "32Gi" } }
        ]
      }
    }
  }
}
```

> 镜像为本地镜像（`video-matting:local`）时需配置节点上 docker 为 `imagePullPolicy: IfNotPresent`（脚本已内置）；推送到 SWR 后改为完整镜像地址。

### 4.2 创建 Service（LoadBalancer，自动创建 ELB）

```
POST https://{apiserver}/api/v1/namespaces/default/services
```

请求体：

```json
{
  "apiVersion": "v1",
  "kind": "Service",
  "metadata": {
    "name": "video-matting-svc",
    "namespace": "default",
    "annotations": {
      "kubernetes.io/elb.class": "union",
      "kubernetes.io/elb.autocreate": "{\"type\":\"public\",\"bandwidth_name\":\"video-matting-bandwidth\",\"bandwidth_size\":100,\"bandwidth_sharetype\":\"PER\",\"bandwidth_chargemode\":\"traffic\"}"
    }
  },
  "spec": {
    "type": "LoadBalancer",
    "selector": { "app": "video-matting" },
    "ports": [ { "port": 8001, "targetPort": 8001 } ]
  }
}
```

> **`kubernetes.io/elb.autocreate` 注解是 CCE 自动创建 ELB 的开关**，不写则 Service 会因找不到 ELB 一直 `Pending`。`type: "public"` 表示创建公网 ELB（带 EIP）。
> CCE Turbo（eni 模式）下 ELB 直通容器，无需 NodePort。

### 4.3 查询 Pod 状态（轮询用）

```
GET https://{apiserver}/api/v1/namespaces/default/pods?labelSelector=app%3Dvideo-matting
```

`items[].status.phase`：`Pending`/`ContainerCreating` → **`Running`**；异常态 `CrashLoopBackOff`/`ImagePullBackOff` 需看日志：

```
GET https://{apiserver}/api/v1/namespaces/default/pods/{pod_name}/log
```

### 4.4 查询 Service 外部地址

```
GET https://{apiserver}/api/v1/namespaces/default/services/video-matting-svc
```

对外地址：`status.loadBalancer.ingress[0].ip`（ELB 的公网 IP）。

---

## 5. SWR 镜像仓库 API

**管理面 Base URL：`https://swr-api.{region}.myhuaweicloud.com`**（2026-11-04 实测：`swr.{region}` 域名调 `/v2/manage/*` 一律 404，manage API 必须走 `swr-api` 前缀；`swr.{region}` 只作 docker registry 拉推端点）。认证：`X-Auth-Token`（Token 方式）或 AK/SK 签名。

### 5.1 生成临时登录指令（docker login 用）

```
POST /v2/manage/utils/secret?projectname={region}
X-Auth-Token: {token}
```

响应 `200`：

- 响应头 **`X-Swr-Dockerlogin`**：完整的 `docker login -u xxx -p xxx swr.{region}.myhuaweicloud.com` 指令，直接在节点上执行即可。
- 响应头 `X-Swr-Dockerlogin` 旁有 `X-Swr-Expireat`（过期时间）。
- 响应体：`{"auths": {"swr.{region}...": {"auth": "<base64>"}}}`（docker config.json 格式）。

> 官方文档：https://support.huaweicloud.com/api-swr/swr_02_0052.html

### 5.2 创建组织（命名空间）

```
POST /v2/manage/namespaces
```

```json
{ "namespace": "video-matting", "creator": "true" }
```

> 组织名全局唯一（同区域），建议用"账号名-业务名"格式。旧接口 `POST /v2/manage/organizations` 已由 namespaces 取代。

### 5.3 查询组织列表

```
GET /v2/manage/organizations
```

### 5.4 节点构建+导出+推送镜像（CCE 节点，无 docker）

**CCE 节点没有 docker（containerd + buildkit）**，全程 ctr/buildkit。2026-11-04 全链路实测（15G 镜像）。

**① buildkit 构建 + docker-tar 导出 —— 必须走 TCP addr（unix socket 会中途断流）**：

```bash
# buildkitd 同时监听 unix + TCP
nohup /usr/local/bin/buildkitd \
  --addr unix:///run/buildkit/buildkitd.sock \
  --addr tcp://127.0.0.1:12345 >/root/vm-build/buildkitd.log 2>&1 &

# 就绪判断不能用 [ -S socket ]（残留死 socket 也通过），必须 GRPC 真探：
buildctl --addr tcp://127.0.0.1:12345 debug info

# 构建并导出 docker tar —— --addr 也走 TCP
buildctl --addr tcp://127.0.0.1:12345 build \
  --frontend dockerfile.v0 --local context=. --local dockerfile=. \
  --output type=docker,name=video-matting:{tag},dest=/root/vm-build/video-matting-{tag}.tar
```

> **坑（实测 3 次复现）**：15G tar 流经 unix socket 时 buildctl↔buildkitd 中途死连接
> （`failed to copy to tar: grpc: the client connection is closing`，5.5G/15G 处断）。
> buildkitd 挂 `--addr tcp://127.0.0.1:12345` 后 buildctl 同走 TCP，一次成功。
> buildkit 缓存（/var/lib/buildkit，42G）跨 daemon 重启存活——改码重建只跑增量层，分钟级。
> **buildkit 的 `type=image,push=true` 无法推 SWR**（oauth 端点 401，buildctl 无传凭据入口），不要尝试。

**② ctr 导入 —— 名字是 FQDN，短名 tag 不到**：

```bash
ctr -a /var/run/containerd/containerd.sock -n k8s.io images import /root/vm-build/video-matting-{tag}.tar
# 导入后实际名字是 docker.io/library/video-matting:{tag}（ctr 不做短名归一化！）
# 宽列显示会把连字符折行显示成 "video matting"，用 images ls -q | sed -n l 看真名。
```

**③ tag + 推送**：

```bash
# 凭据来自 5.1 的 X-Swr-Dockerlogin（-u xxx -p yyy），存节点 /root/_swr_creds.sh (chmod 600)
ctr -a /var/run/containerd/containerd.sock -n k8s.io images tag \
  docker.io/library/video-matting:{tag} \
  swr.{region}.myhuaweicloud.com/mcn-poc/video-matting:{tag}
ctr -a /var/run/containerd/containerd.sock -n k8s.io images push \
  --user "{用户名}:{密码}" swr.{region}.myhuaweicloud.com/mcn-poc/video-matting:{tag}
```

- `ctr push --user` 直接带临时凭据，无需 docker login / hosts-dir 配置。
- **SWR 临时凭据有效期 24h**——过期后 push 报 401（oauth token fetch 阶段），重跑 5.1 生成新的即可。带过期时间戳存 `/root/_swr_creds.sh`。
- **增量推送极快**：SWR 按层去重，仅差 1 个代码层的 20261104 推送仅 36s（首推 20261103 全量 14.3G 用时 158s）。
- **容器内验证代码用 `ctr run --rm`（不带 --null-io）**：`--null-io` 会吞掉容器全部 stdout，只剩 OUTER_RC=0 假象：
  ```bash
  ctr -a /var/run/containerd/containerd.sock -n k8s.io run --rm \
    docker.io/library/video-matting:{tag} fusecheck sh -c 'grep -n "保险丝" /code/main.py'
  ```
- 若节点装了 docker（自建 ECS 构建机）才用经典三连：`docker login` → `docker tag` → `docker push`。

推送后工作负载镜像地址改为 `swr.{region}.myhuaweicloud.com/{namespace}/video-matting:{tag}`（CCE 节点默认可免密拉取同账号 SWR 镜像）。当前正典 tag：**`20261104`**（隔离保险丝版：OSS_API_DOMAIN 默认 127.0.0.1:1、OSS_BUCKET 默认 obs-mcn、insta360 引用清零，容器内 grep 已验证）。

---

## 6. ECS 辅助 API

节点公网 IP 一般可直接从 CCE 节点详情（3.4 的 `status.publicIP`）获得；若为空，可通过 `status.serverId` 查 ECS：

```
GET https://ecs.{region}.myhuaweicloud.com/v1/{project_id}/cloudservers/{server_id}
GET https://ecs.{region}.myhuaweicloud.com/v1/{project_id}/cloudservers/{server_id}/os-interface
```

`os-interface` 返回 `interfaceAttachments[].fixed_ips[].ip_address`（私网 IP）；公网 IP 用 `cloudservers/{server_id}` 详情里的 `public_ips` 或到 EIP 服务（`GET /v1/{project_id}/publicips`）按绑定实例过滤。

---

## 7. video-matting 服务自身接口

部署完成后对外暴露的 HTTP 接口（经 ELB 公网 IP + 8001 端口访问）。

### 7.1 健康检查

```
GET http://{service_ip}:8001/health
```

- 模型就绪：`200` → `{"status": "ok", "output": ""}`
- 模型加载中：`503`（继续轮询）

### 7.2 预热（离线加载 Florence-2 与 SAM2 到显存）

```
POST http://{service_ip}:8001/initialize
```

响应（超时至少设 60s，建议 90s）：

```json
{
  "status": "warmed",
  "cached": false,
  "duration_ms": 45000,
  "florence_ms": 25000,
  "sam2_ms": 20000,
  "compile": false
}
```

### 7.3 URL 推理（NDJSON 流式响应）

```
POST http://{service_ip}:8001/video/segment_url
Content-Type: application/json
```

```json
{
  "video_url": "https://example.com/test.mp4",
  "mode": "auto",
  "oss_bucket": "insta360-app-hz",
  "oss_scene": "aiMatting",
  "oss_download_expiration": 604800
}
```

响应为 **NDJSON 流**（逐行 JSON），典型顺序：

```
{"status": "received", ...}
{"meta": {"width": 1920, "height": 1080, "fps": 30, "frames": 300, ...}}
{"data": {"frame": 0, "mask_url": "...", "matting_url": "..."}}
{"data": {...}}
{"finish": {"status": "done", "frames": 300, "duration_ms": ...}}
```

### 7.4 文件上传推理

```
POST http://{service_ip}:8001/video/segment_stream
Content-Type: multipart/form-data
```

表单字段：`file`（视频文件）、`mode`（`auto`/`manual`）。响应同为 NDJSON 流。

---

## 8. 常用错误码

| HTTP 码 | 含义 | 排查 |
|---------|------|------|
| 401 | 认证失败 | AK/SK 错误、Token 过期（24h）、签名时间偏差过大（检查本机时钟） |
| 403 | 权限不足 / 账号欠费 | IAM 用户需具备 CCE Administrator / VPC Administrator 权限；检查账户余额 |
| 404 | 资源不存在或接口路径错误 | 核对 project_id、cluster_id、区域 endpoint |
| 409 | 资源冲突 | 同名集群/节点/安全组已存在 |
| 429 | 请求过频 | 降低轮询频率 |
| 500/502/503 | 服务端异常 | 重试；CCE 创建任务可查 jobID 状态 |

配额类报错（`CCC.1022` 等）需到控制台提工单扩容或清理资源。
