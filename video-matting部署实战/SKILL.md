---
name: huawei-cloud-cce-deploy
description: 通过华为云 API 自动化部署 video-matting 视频抠像服务到 CCE Turbo 集群。完整覆盖 IAM 认证→建网络→建集群→创建 GPU 节点→构建镜像→创建工作负载→健康检查→预热→接口验证的全流程。当用户提到在华为云部署 video-matting、CCE Turbo 部署 GPU 服务、自动化部署到华为云时使用此技能。
---

# 华为云 CCE Turbo 自动化部署 video-matting 服务

通过华为云 API 自动化完成 video-matting（基于 Florence-2-Large 与 SAM2.1 的视频主体分割服务）在 CCE Turbo 集群上的全流程部署。

## 适用场景

- 用户需要在华为云 CCE Turbo 上部署 video-matting 服务
- 用户希望通过 API 自动化完成集群创建、节点创建、镜像构建、工作负载部署、健康检查、预热和接口验证
- 用户已有华为云账号和 AK/SK（或 IAM 用户密码），需要端到端部署
- **用户说"部署一套一样的/一毛一样的集群"→ 走下面的复刻模式，不再问参数**

## 复刻模式（Replica Mode）——一句话部署同款集群

> 2026-11-03/04 主环境全流程实测固化；2026-11-05 三建环境（自由节点+无 NAT+路线A）再验证。触发即按本节执行，只有两处需要确认：
> 新集群名（默认 `video-matting-cluster-<日期>`）和白名单 IP（默认用户电脑出口 `111.119.234.6`）。

### 固化参数表

| 项 | 固化值 | 备注 |
|---|---|---|
| 镜像 | `swr.cn-east-3.myhuaweicloud.com/mcn-poc/video-matting:20261104`（隔离保险丝版：默认 OSS_API_DOMAIN=127.0.0.1:1 / OSS_BUCKET=obs-mcn / insta360 引用清零，容器内 grep 已验证） | **跳过阶段四**，CCE 同账号免密拉取；旧 20261103 保留在 SWR 可回滚 |
| 区域/项目 | cn-east-3（上海一）/ `ef13bf432f15434e92104390d62dc5bf` | |
| 节点规格 | `pi5.4xlarge.4`（**11-04 实测修正**，旧记录 c7i.4xlarge.2.l20 在本区不存在） | 实卡 **NVIDIA L2 24GB**（23034 MiB，cc 8.9，16核64G，1卡/节点）；AZ 只在 **cn-east-3d**；OS=Huawei Cloud EulerOS 2.0 **公共镜像（无预装驱动，驱动走下方路线A）**，sshKey=video-matting-key，数据盘 SAS 200G。旧 NodeImageID=8553a184 驱动随镜像路线已弃用（布局坑多、非界面口径） |
| 双卡供给 | **cn-east-3 无 2 卡 L2 规格**（11-04 全量 2003 规格盘点：L2 线只有 pi5.4xlarge.4=1卡、pi5.24xlarge.4=**6卡**/96核384G 且 cn-east-3d 常态 sellout） | gpu:2 实测必然 Pending（不跨节点拼卡）；要双卡=等 6 卡补货（贵~6×）或换区域 |
| 节点系统盘/数据盘 | 100G / 200G，EIP 100M | |
| 工作负载 | 直接 `kubectl apply assets/vm_workload.yaml`（占位符 2 个：SWR 镜像地址、OSS_API_DOMAIN） | 含单卡滚动策略/shm 32Gi/探针/nodeSelector 全套 |
| 并发/编译 | `GPU_CONCURRENCY_LIMIT=1`、`SAM2_COMPILE=false` | 24G 显存硬约束 |
| 数据隔离 | `OSS_API_DOMAIN` **必须**钉死垫片地址（见铁律） | env 不设 = 代码默认值 = 客户生产域名 |
| 公网入口 | 共享型 ELB → `scripts/elb_acl.py` 挂 IP 白名单（默认放行 111.119.234.6） | 实测：外部非白名单 IP TCP 握手都不过；VPC 内访问不走监听器属正常 |
| 结果/数据集 | 自有桶 `obs-mcn`（cn-east-3）；E2E 视频用 `obs-mcn/video-matting-test/test_person.mp4` 的 V2 预签名 URL | **绝不使用客户任何域名/桶/凭据** |

### 数据隔离铁律（每次部署必须过的门，2026-11-04 用户铁令）

镜像内代码的 `OSS_API_DOMAIN` 默认值指向**客户生产 OSS**。因此：

1. 节点上起垫片 `assets/obs_shim.py`（`OBS_SHIM_ADVERTISE_IP=<节点VPC IP>`，伪造存储 API，结果只落节点本地目录，客户 OSS 物理不可达）；
2. Deployment env 显式 `OSS_API_DOMAIN=http://<节点VPC IP>:8001`；
3. 数据集与输出结果只进自有桶 `obs-mcn`（V2 签名 PUT；测试视频也用自有桶预签名 URL）；
4. 除垫片模拟的 API 形状外，部署流程不出现客户任何信息。

### GPU 驱动 = 路线A（公共镜像 + gpu-beta 插件装驱动）——2026-11-05 自由节点全链路验证

用户指定口径：**驱动必须走 CCE 插件（界面安装等效），不用驱动预装镜像**。节点 Active 后按此装 gpu-beta（参考脚本 `work/build/sh2_gpu_fix_url.py`）：

1. **驱动 URL 必须显式给**（否则自由节点/池节点两难不同）：
   - **池节点**：operator 自动填华为桶 URL，默认值即可；
   - **自由节点**（非 nodepool，`cce.cloud.com/cce-nodepool-id=""`）：operator 生成的 NvidiaNodeConfiguration 为空 → installer 无 URL 直接 CrashLoop，**必须显式 PUT** `values.custom.nvidia_driver_download_url`。
2. **`is_driver_from_nvidia` 语义（踩过 SignatureDoesNotMatch 才懂）**：`false` = URL 被当**相对路径**拼到 `obs.{region}.myhuaweicloud.com/cce.{region}/cce-addon/GPU-driver/` 基路径后（自建桶中转必炸）；`true` = **原样透传**进 DS env `NVIDIA_DRIVER_DOWNLOAD_URL`。自定义任何桶地址都要 true。
3. **驱动源两选**：① 华为 OBS 真源 `https://hgcs-drivers-{region}.obs.{region}.myhuaweicloud.com/release/driver/tesla/NVIDIA-Linux-x86_64-{版本}.run`（匿名可拉，内网 DNS 直达，免中转）；② 自有桶 `obs-mcn` V2 签名 PUT + 48h 预签名 GET URL 中转（`build/relay_driver_win5.py`；Windows 下载 NVIDIA CDN 后上传——**Akamai 403 华为云 IP**，节点直下必 403）。两者都不需要 NAT（OBS 走内网）。
4. **改插件 values = 卸了重装**：同版本 PUT 报 400 `unsupported upgrade type` → DELETE 等卸净（GET 404）+ POST（annotations `addon.install/type: install`）。重装后 helm 会把 coredns/everest-csi-controller/nvidia-operator 副本拉回 2 → 单节点集群必须重压 1。
5. **验证口径（插件装完必验，官网标准）**：节点容量 `nvidia.com/gpu=1` + 节点侧 `cd /usr/local/nvidia/bin && ./nvidia-smi` + 容器侧 `cd /usr/bin && ./nvidia-smi`（device-plugin 镜像起临时 Pod 即可）。
6. **无 NAT 环境补充**：Turbo ENI Pod 无公网（节点 EIP 不服务 Pod）；OBS/SWR 内网 endpoint 全通，只有公网源（NVIDIA CDN/GitHub release）不可达。

### 复刻流程（对比全流程：跳过阶段四，其余相同）

认证（阶段一）→ 建网络+集群（阶段二，VPC/子网 auto_create；**建完子网立即改 DNS**：CCE 默认子网 DNS `100.125.36.53/100.125.5.151` 在部分 VPC 主位不可达 → cce-agent 下载失败、节点永远卡 Installing。修法=neutron `PUT /v2.0/subnets/{neutron_subnet_id} {"subnet":{"dns_nameservers":["100.125.1.250","100.125.36.53"]}}`（v1 subnet 无 PUT；ENI 子网同改）。已卡住的节点：SSH 修 `/etc/resolv.conf` 首选 100.125.1.250 + nmcli `ipv4.dns ...;ipv4.ignore-auto-dns yes` 后重跑 `bash /var/lib/cloud/instance/scripts/runcmd`，~1 分钟即 Active）→ 建 GPU 节点（阶段三，规格用上表，公共镜像）→ **装 gpu-beta 插件（路线A，见上节）** → **跳过镜像构建（阶段四）** → 工作负载（阶段五，apply `assets/vm_workload.yaml` 或 deploy.py `--image` 传 SWR 地址，**必须带 `OSS_API_DOMAIN` env**）→ 健康检查+预热（阶段六/七）→ **收尾三件事**：① 起垫片（`OBS_SHIM_ADVERTISE_IP=<节点VPC IP>` 必带，否则预签名 URL 是废的）并确认 env 已钉死；② `scripts/elb_acl.py` 挂白名单；③ 交付时说明两个已知限制——单 Pod 连续第二个请求必 OOM（代码 finally 缺 `del`+`torch.cuda.empty_cache()`，重启 Pod 恢复，已反馈客户修码）；长视频需开 `SAM2_OFFLOAD_VIDEO_TO_CPU=true`（24G 单卡跑不了 >22G 显存的视频，两卡拼显存不可行，代码是单卡逻辑）。

运维陷阱（2026-11-05 实测）：**Installing 态节点 DELETE 被 403 拒**（CCE.01403001，Abnormal 态可删；DELETE 429 "Resource locked"→轮询到 404=已删）；**kubectl logs/exec 超时**=SG 缺 apiserver→kubelet `tcp/10250` 入方向（放行 VPC 网段）；EIP 复用会换 host key（`ssh-keygen -R` 后 accept-new）；clustercert 请求体 `{"duration":1825}`；垫片端口 8001 的 SG 规则只放 VPC 网段（无鉴权上传口绝不露公网）；**SWR 拉取 401 anonymous**=default SA 没自动挂 `imagePullSecrets`（三建环境实测：`default-secret` 在但 SA 没引用）→ `kubectl patch sa default -n default --patch-file '{"imagePullSecrets":[{"name":"default-secret"}]}'` 后**删 Pod 重建**才生效（存量 Pod 不会补）；**elb.autocreate 注解必须是单行合法 JSON**——注释写进 `>` 折叠块标量会变成内容，admission 报 `invalid character '#' after top-level value`（注释放块外或整个注解用单引号单行）。

## SKILL 目录结构

```
huawei-cloud-cce-deploy/
├── SKILL.md                    ← 本文件，工作流定义（含复刻模式）
├── scripts/
│   ├── auth.py                 ← IAM 认证：Token 获取 + SDK-HMAC-SHA256 签名
│   ├── deploy.py               ← 部署编排（create-network/create-cluster/create-node/create-workload/swr-login/wait-*）
│   ├── verify.py               ← 健康检查、预热、推理/上传验证
│   └── elb_acl.py              ← ELB 公网监听器挂 IP 白名单（复刻模式收尾必跑）
└── assets/
    ├── deploy_template.md      ← 部署参数模板（用户填写；OSS 段=数据隔离铁律）
    ├── api_reference.md        ← 华为云 API 参考清单（含请求体示例，API 细节以它为准）
    ├── vm_workload.yaml        ← 已验证工作负载基准（Deployment+Service 全套固化配置）
    └── obs_shim.py             ← 数据隔离垫片（伪造存储 API，结果落本地，客户 OSS 不可达）
```

## 执行前检查

在开始之前，必须确认以下信息已提供。读取 `assets/deploy_template.md` 模板，要求用户填写以下必填项：

### 认证信息（必填）

| 参数 | 说明 | 示例 |
|------|------|------|
| `HWC_AK` | 华为云 Access Key ID | `AKIDxxxxxxxxxx` |
| `HWC_SK` | 华为云 Secret Access Key | `xxxxxxxxxxxxxxxxxx` |
| `HWC_DOMAIN_ID` | 账号 ID（租户 ID） | `0a1b2c3d4e5f...` |
| `HWC_IAM_USER_ID` | IAM 用户 ID（子账号时需要） | `0a1b2c3d4e5f...` |
| `HWC_IAM_USERNAME` / `HWC_IAM_PASSWORD` | IAM 用户名/密码（密码方式换 Token 时需要） | — |
| `HWC_REGION` | 区域代码 | `cn-east-3` |
| `HWC_PROJECT_ID` | 项目 ID | `0a1b2c3d4e5f...` |

### 网络信息（必填，如已有则填 ID，如无则填 `auto_create`）

| 参数 | 说明 |
|------|------|
| `VPC_ID` | 已有 VPC ID，或 `auto_create` |
| `SUBNET_ID` | 已有子网 ID（网络ID），或 `auto_create` |
| `ENI_SUBNET_ID` | Turbo ENI 子网 ID；留空复用 SUBNET_ID |
| `SECURITY_GROUP_ID` | 已有安全组 ID，或 `auto_create` |
| `AVAILABLE_ZONE` | 可用区，如 `cn-east-3a`（API 创建节点不支持 random） |

### 集群与节点配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `CLUSTER_NAME` | `video-matting-cluster` | CCE 集群名称 |
| `CLUSTER_VERSION` | `v1.36` | 集群版本 |
| `CLUSTER_FLAVOR` | `cce.s2.small` | 集群规格（s1=单控制节点 / s2=三控制节点高可用） |
| `NODE_NAME` | `video-matting-node-01` | 节点名称 |
| `NODE_FLAVOR` | `pi5.4xlarge.4` | GPU 节点规格（cn-east-3 实测=1×L2 24G；先用 ECS v2.1 flavors/detail 翻页盘点确认区域供给） |
| `NODE_COUNT` | `1` | 节点数量（长视频建议 2） |
| `NODE_OS` | `EulerOS 2.9` | 节点操作系统 |
| `NODE_ROOT_VOLUME_SIZE` | `100` | 系统盘 GB |
| `NODE_DATA_VOLUME_SIZE` | `200` | 数据盘 GB |
| `SSH_KEY_PAIR` | — | SSH 密钥对名称（需预先在控制台创建，用于登录节点构建镜像） |
| `EIP_BANDWIDTH` | `100` | 节点 EIP 带宽 Mbps |
| `SHM_SIZE` | `32Gi` | 容器 /dev/shm 大小 |

### 镜像与服务配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `SOURCE_PACKAGE_URL` | — | video-matting 源码包下载地址 |
| `IMAGE_NAME` | `video-matting` | 镜像名称 |
| `IMAGE_TAG` | `local` | 镜像标签 |
| `SWR_REGISTRY` | `auto_create` | SWR 仓库：`auto_create`（自动登录+推送）或已有地址 |
| `SERVICE_PORT` | `8001` | 服务端口 |
| `GPU_CONCURRENCY_LIMIT` | `1` | GPU 并发限制 |
| `SAM2_COMPILE` | `false` | SAM2 编译开关 |
| `GPU_LIMIT` | `1` | 工作负载申请 GPU 数量 |
| `ELB_BANDWIDTH` | `100` | ELB 公网带宽 Mbps |

### OSS 配置（数据隔离铁律，必填）

| 参数 | 说明 |
|------|------|
| `OSS_API_DOMAIN` | **必须显式钉死为自有垫片地址**（`http://<节点VPC IP>:8001`，配 assets/obs_shim.py）。镜像代码默认值=客户生产域名，**不设此 env = 结果写客户生产桶，绝对禁止** |
| `OBS_OWN_BUCKET` | 自有结果桶，固定 `obs-mcn`（cn-east-3）。数据集与输出只进这里 |

当用户输入不符合要求时，停止执行并向用户确认缺失项。

## 工作流程

完整部署分为 **7 个阶段**。所有脚本依赖 `requests` 库（`pip install requests`）。所有华为云 API 的完整定义、请求体示例和错误码见 `assets/api_reference.md`——**遇到 API 字段疑问时以该文件为准**。

---

### 阶段一：IAM 认证与 Token 获取

**输入**：用户提供的 AK / SK / Domain ID / Region / Project ID（可选 IAM 用户名/密码）
**输出**：IAM Token（有效期 24 小时）+ AK/SK 签名能力

1. 执行 `scripts/auth.py` 获取 Token：
   ```bash
   python scripts/auth.py \
     --ak "$HWC_AK" --sk "$HWC_SK" \
     --domain-id "$HWC_DOMAIN_ID" \
     --region "$HWC_REGION" \
     --project-id "$HWC_PROJECT_ID" \
     --output token.json
   ```
   - 脚本优先尝试 AK/SK 换 Token；失败时自动降级为"签名自检"并给出明确指引。
   - 提供 `--iam-user/--password` 时改用官方密码方式（`POST /v3/auth/tokens`，Token 在响应头 `X-Subject-Token`）。
2. AK/SK 签名（SDK-HMAC-SHA256）由 `auth.py` 的 `sign_request()` 实现，`deploy.py` 所有调用自动复用——即使 Token 不可用也能全程签名调用。
3. 验证失败时提示用户检查 AK/SK、账号是否欠费/冻结、本机时钟偏差。

**关键 API**：`POST https://iam.{region}.myhuaweicloud.com/v3/auth/tokens`（详见 api_reference.md §1）

**检查点**：`token.json` 包含 `token` 和 `project_id`，或签名自检通过。

---

### 阶段二：创建 CCE Turbo 集群

**输入**：Token/签名 + 集群配置 + 网络 ID（或自动创建网络）
**输出**：集群 ID + 集群状态 `Available`

1. **网络准备**（如 VPC_ID / SUBNET_ID 为 `auto_create`）：
   ```bash
   python scripts/deploy.py create-network \
     --token-file token.json \
     --vpc-id auto_create --subnet-id auto_create \
     --az "$AVAILABLE_ZONE" --service-port "$SERVICE_PORT" \
     --output network.json
   ```
   - 创建 VPC（`192.168.0.0/16`）、子网（`192.168.0.0/24`，轮询到 `ACTIVE`）、安全组（放通 22 与 8001 入方向）。

2. **创建 CCE Turbo 集群**：
   ```bash
   python scripts/deploy.py create-cluster \
     --token-file token.json \
     --cluster-name "$CLUSTER_NAME" \
     --cluster-version "$CLUSTER_VERSION" \
     --vpc-id "$VPC_ID" --subnet-id "$SUBNET_ID" \
     --sg-id "$SECURITY_GROUP_ID" \
     --output cluster.json
   ```
   - 请求体关键字段：`spec.category: "Turbo"` + `spec.containerNetwork.mode: "eni"` + `spec.eniNetwork.subnets`（Turbo 必需组合）；节点网络在 `spec.hostNetwork`（vpc/subnet）。

3. **轮询集群状态**：
   ```bash
   python scripts/deploy.py wait-cluster --token-file token.json \
     --cluster-id "$CLUSTER_ID" --output cluster.json
   ```
   每 30 秒一次，直到 `status.phase = Available`（通常 5~15 分钟）。

**关键 API**（api_reference.md §2、§3.1-3.2）：
- `POST /api/v3/projects/{project_id}/clusters` — 创建集群
- `GET /api/v3/projects/{project_id}/clusters/{cluster_id}` — 查询集群
- `POST /v1/{project_id}/vpcs`、`POST /v1/{project_id}/subnets`、`POST /v1/{project_id}/security-groups`（vpc 服务）

**检查点**：集群 `Available`，`cluster.json` 包含 `cluster_id`。

---

### 阶段三：创建 L20 GPU 节点

**输入**：集群 ID + 节点配置
**输出**：节点 ID + 节点状态 `Active` + 节点公网 IP

1. **创建 GPU 节点**（自动新购 EIP）：
   ```bash
   python scripts/deploy.py create-node \
     --token-file token.json \
     --cluster-id "$CLUSTER_ID" \
     --node-name "$NODE_NAME" --node-flavor "$NODE_FLAVOR" \
     --node-count "$NODE_COUNT" --node-os "$NODE_OS" \
     --root-volume-size "$NODE_ROOT_VOLUME_SIZE" \
     --data-volume-size "$NODE_DATA_VOLUME_SIZE" \
     --ssh-key "$SSH_KEY_PAIR" \
     --az "$AVAILABLE_ZONE" --subnet-id "$SUBNET_ID" \
     --eip-bandwidth "$EIP_BANDWIDTH" \
     --output node.json
   ```

2. **轮询节点状态**：
   ```bash
   python scripts/deploy.py wait-node --token-file token.json \
     --cluster-id "$CLUSTER_ID" --node-id "$NODE_ID" --output node.json
   ```
   每 30 秒一次，直到 `status.phase = Active`（通常 5~10 分钟）。公网 IP 从节点详情 `status.publicIP` 读取。

3. **验证 GPU 可用**：SSH 登录节点（root + 密钥对）执行 `nvidia-smi` 确认 GPU 正常。

**关键 API**（api_reference.md §3.3-3.4）：
- `POST /api/v3/projects/{project_id}/clusters/{cluster_id}/nodes` — 创建节点（`spec.publicIP: {count, eip}` 自动新购 EIP）
- `GET /api/v3/projects/{project_id}/clusters/{cluster_id}/nodes/{node_id}` — 查询节点（`status.publicIP` / `status.serverId`）

**检查点**：节点 `Active`，`nvidia-smi` 正常，`node.json` 包含 `node_id` 和 `public_ip`。

---

### 阶段四：构建 Docker 镜像

> **复刻模式跳过本阶段**（镜像已在 SWR：`swr.cn-east-3.myhuaweicloud.com/mcn-poc/video-matting:20261104`）。
> **CCE 节点没有 docker**（containerd + buildkit，硬装 docker 会和 containerd 冲突）。下面的 docker 流程仅适用于自建 docker 构建机；CCE 节点上的实测路径是：buildkitd 构建 → `ctr images import` 导入 containerd → `ctr images push --user` 推 SWR（见 api_reference.md §5.4）。

**输入**：节点公网 IP + SSH 密钥 + 源码包地址
**输出**：镜像 `video-matting:local` 构建完成

1. **SSH 登录 GPU 节点**（root + SSH 密钥对；Windows 下可用 `ssh -i key.pem root@{public_ip}`）。

2. **下载源码包并解压**：
   ```bash
   wget "$SOURCE_PACKAGE_URL" -O /tmp/video-matting-source.zip
   unzip /tmp/video-matting-source.zip -d /opt/video-matting
   cd /opt/video-matting
   ```

3. **构建镜像**：
   ```bash
   docker build -t video-matting:local .
   ```
   > 构建会安装 FFmpeg/Python 依赖，拉取 SAM2 CUDA 扩展，下载 SAM2.1 权重和 Florence-2-Large 模型。镜像较大（10GB+），首次构建耗时长。

4. **（可选）推送到 SWR**（多节点部署或镜像持久化时）：
   ```bash
   # 本地生成临时登录指令（响应头 X-Swr-Dockerlogin 即完整 docker login 命令）
   python scripts/deploy.py swr-login --token-file token.json
   # 在节点上执行该 docker login 指令后：
   docker tag video-matting:local swr.$HWC_REGION.myhuaweicloud.com/{namespace}/video-matting:local
   docker push swr.$HWC_REGION.myhuaweicloud.com/{namespace}/video-matting:local
   ```
   推送后阶段五的 `--image` 改用 SWR 完整地址（CCE 节点同账号 SWR 免密拉取）。

5. **验证镜像存在**：`docker images | grep video-matting`

**关键 API**（api_reference.md §5）：
- `POST https://swr.{region}.myhuaweicloud.com/v2/manage/utils/secret` — 生成临时登录指令（响应头 `X-Swr-Dockerlogin`）
- `POST /v2/manage/namespaces` — 创建组织（可选）

**检查点**：`docker images` 中存在 `video-matting:local`，镜像大小正常。

---

### 阶段五：创建 GPU 工作负载

**输入**：集群 ID + 镜像名称 + 服务配置
**输出**：Deployment + LoadBalancer Service 创建成功，Pod `Running`，拿到 ELB 公网 IP

> CCE 没有独立的 Deployment REST 接口——先调 CCE `clustercert` 拿客户端证书，再走集群原生 Kubernetes API（脚本已自动完成）。

1. **一条命令完成**（证书获取 → 建 Deployment → 建 Service → 轮询 Pod → 取 ELB IP）：
   ```bash
   python scripts/deploy.py create-workload \
     --token-file token.json \
     --cluster-id "$CLUSTER_ID" \
     --image "video-matting:local" \
     --service-port "$SERVICE_PORT" \
     --gpu-limit "$GPU_LIMIT" \
     --shm-size "$SHM_SIZE" \
     --elb-bandwidth "$ELB_BANDWIDTH" \
     --env PORT=$SERVICE_PORT \
     --env GPU_CONCURRENCY_LIMIT=$GPU_CONCURRENCY_LIMIT \
     --env SAM2_COMPILE=$SAM2_COMPILE \
     --output workload.json
   ```

2. **工作负载关键配置**（脚本内置，等价 YAML）：
   - Deployment：`replicas: 1`，`resources.limits."nvidia.com/gpu": 1`，`imagePullPolicy: IfNotPresent`（本地镜像必配），env 注入 PORT/GPU_CONCURRENCY_LIMIT/SAM2_COMPILE，`emptyDir`（medium: Memory, sizeLimit: 32Gi）挂载 `/dev/shm`。
   - Service：`type: LoadBalancer` + 注解 `kubernetes.io/elb.autocreate`（**CCE 自动创建公网 ELB 的开关，不写会一直 Pending**；`type: public` 时 **必须带 `"eip_type": "5_bgp"`**，CCE admission webhook 校验强制，缺了整个 Service 被拒——deploy.py 已内置）。
   - 数据隔离铁律：`--env OSS_API_DOMAIN=http://<节点VPC IP>:8001`（垫片地址）**必传**，不传等于放任结果写客户生产桶。
   - 单卡节点多副本/滚动：必须 `maxSurge=0/maxUnavailable=1`（新 Pod 抢不到 GPU 会卡 ContainerCreating）。

3. **轮询与取址**：脚本每 15 秒查 Pod（`labelSelector=app=video-matting`）直到 `Running`，再从 Service `status.loadBalancer.ingress[0].ip` 取 ELB 公网 IP。

**关键 API**（api_reference.md §3.5、§4）：
- `POST /api/v3/projects/{project_id}/clusters/{cluster_id}/clustercert` — 获取客户端证书
- `POST https://{apiserver}/apis/apps/v1/namespaces/default/deployments` — 创建 Deployment
- `POST https://{apiserver}/api/v1/namespaces/default/services` — 创建 Service（带 elb.autocreate 注解）
- `GET .../pods?labelSelector=app%3Dvideo-matting` — 轮询 Pod

**检查点**：Pod `Running`，`workload.json` 包含 `service_ip`。

---

### 阶段六：健康检查

**输入**：Service 外部访问地址（ELB 公网 IP）
**输出**：健康检查通过，返回 HTTP 200

1. **等待模型加载**：服务启动离线加载 Florence 和 SAM2，可能需要 30~60 秒。

2. **轮询健康检查**：
   ```bash
   python scripts/verify.py health-check \
     --host "$SERVICE_IP" --port "$SERVICE_PORT" \
     --max-retries 30 --interval 10
   ```

3. **验证响应**：
   - 就绪返回 `200`：`{"status":"ok","output":""}`
   - 加载中返回 `503`：继续等待
   - 连续 30 次失败：报告错误，建议查看 Pod 日志

**关键操作**：`GET http://{service_ip}:{port}/health`（api_reference.md §7.1）

**检查点**：健康检查返回 200，`status` 为 `ok`。

---

### 阶段七：预热与接口验证

**输入**：Service 外部访问地址
**输出**：预热成功 + 推理接口返回完整 NDJSON 流

1. **调用预热接口**（超时至少 60s）：
   ```bash
   python scripts/verify.py warmup \
     --host "$SERVICE_IP" --port "$SERVICE_PORT" --timeout 90
   ```
   - `POST /initialize`，预期 `{"status":"warmed","cached":false,"duration_ms":...,"florence_ms":...,"sam2_ms":...,"compile":false}`

2. **验证推理接口（可选）**：
   ```bash
   python scripts/verify.py test-inference \
     --host "$SERVICE_IP" --port "$SERVICE_PORT" \
     --video-url "$TEST_VIDEO_URL" --mode auto
   ```
   - `POST /video/segment_url`，脚本逐行解析 NDJSON，验证包含 `status` → `meta` → `data` → `finish` 完整流程。

3. **验证文件上传接口（可选）**：
   ```bash
   python scripts/verify.py test-upload \
     --host "$SERVICE_IP" --port "$SERVICE_PORT" \
     --file ./test.mp4 --mode auto
   ```
   - `POST /video/segment_stream`（multipart 上传）。

**关键操作**（api_reference.md §7.2-7.4）：`POST /initialize`、`POST /video/segment_url`、`POST /video/segment_stream`

**检查点**：预热返回 `warmed`，推理流包含 `finish`，结果写入 `verify_result.json`。

---

## 部署产出

任务完成后，告知用户以下信息：

1. **集群信息**：集群 ID、集群名称、集群版本、API Server 地址
2. **节点信息**：节点 ID、节点公网 IP、GPU 型号、GPU 数量
3. **服务信息**：Service 外部访问 IP、服务端口、工作负载名称
4. **验证结果**：健康检查状态、预热耗时、推理测试结果
5. **配置文件**：所有中间产物保存在工作目录中：
   - `token.json` — IAM Token（敏感，勿提交）
   - `network.json` — 网络资源 ID
   - `cluster.json` — 集群信息
   - `node.json` — 节点信息
   - `workload.json` — 工作负载与 Service 地址
   - `verify_result.json` — 验证结果

## 错误处理

| 阶段 | 常见错误 | 处理方式 |
|------|----------|----------|
| 认证 | Token 获取失败 | 检查 AK/SK 是否正确，账号是否欠费或被冻结；时钟偏差过大会导致签名失败 |
| 网络 | 子网一直 PENDING | 检查 VPC 配额、CIDR 是否与已有子网冲突 |
| 集群 | 创建超时/失败 | 查看集群事件与 jobID 任务状态，检查配额（每区域默认 5 个集群）、CCE 委托是否已配置 |
| 节点 | GPU 规格不可用 | 先盘点供给：ECS `GET /v2.1/{pid}/flavors/detail` 翻页(marker)过滤，读 extra_specs 的 `info:gpus`(卡数/卡型) 与 `cond:operation:az`(可售状态,如 cn-east-3d(sellout))；cn-east-3 的 L2 线=pi5.4xlarge.4(1卡)/pi5.24xlarge.4(6卡) |
| 镜像 | 构建失败 | SSH 登录节点查看 docker build 日志，检查节点能否访问 GitHub/ModelScope |
| 工作负载 | Service 一直 Pending | 缺少 `kubernetes.io/elb.autocreate` 注解或 ELB 配额不足 |
| 工作负载 | Pod Pending: `Insufficient nvidia.com/gpu` | `nvidia.com/gpu: N` 正整数=整卡调度且**只在单节点内分配，不跨节点拼卡**(doc cce_10_0345)；N>1 需存在单节点 ≥N 卡(2026-11-04 实测:两台单卡节点改 2 → Pending,已回滚)；requests 填了必须=limits；换节点部署时 image 必须用 SWR 全路径(本地 tag 只在导入它的节点) |
| 工作负载 | Pod CrashLoopBackOff | 查看 Pod 日志，检查 GPU 资源、镜像名、显存 |
| 工作负载 | Pod ImagePullBackOff | 本地镜像需 `imagePullPolicy: IfNotPresent`；或推送 SWR 后改用完整地址 |
| 健康检查 | 503 持续 | 模型加载失败，检查模型目录、CUDA 状态、Pod 日志 |
| 预热 | 超时 | 增加超时到 90 秒以上，确认 SAM2_COMPILE=false |
| 推理 | CUDA OOM | 保持 GPU_CONCURRENCY_LIMIT=1，降低 MAX_PROCESS_DIM 或用 SAM2_MODEL_SIZE=small |
| 推理 | 第二个请求必 OOM（与视频长短无关） | 代码泄漏：finally 只有 reset_state，缺 del+empty_cache，请求后仍占 17.58G。重启 Pod 恢复；已反馈客户修码，修复前按"一 Pod 一请求"运营 |
| 镜像 | CCE 节点 docker 命令不存在 | 节点是 containerd+buildkit；导入用 ctr，推送用 ctr push --user（§5.4），勿装 docker |
| 镜像 | buildctl 导出 docker tar 中途断（`client connection is closing`） | 15G 流经 unix socket 必断（3 次复现）；buildkitd 加 `--addr tcp://127.0.0.1:12345`、buildctl 同走 TCP（§5.4①）。就绪判断用 `buildctl debug info`，勿信 `[ -S socket ]`（残留死 socket） |
| 镜像 | buildkit `type=image,push=true` 推 SWR 401 | buildctl 无传凭据入口，SWR oauth 不认；正确路径=导 tar→ctr import→ctr tag(FQDN!)→ctr push --user（§5.4） |
| 镜像 | ctr tag 报 not found / import 后"名字带空格" | ctr 不做短名归一化，import 后真名=`docker.io/library/video-matting:{tag}`，tag/push 必须用 FQDN；宽列显示折行是假象，`images ls -q \| sed -n l` 验真名 |
| 镜像 | ctr push 401（oauth token fetch 阶段） | SWR 临时凭据 24h 过期；重跑 §5.1 生成新凭据即可（增量层去重，重推很快） |
| 镜像 | `ctr run` 看不到容器输出 | `--null-io` 吞掉全部 stdout；去掉它，用 `ctr run --rm <FQDN> name sh -c '...'`（§5.4④） |
| SWR | swr.{region} 调 /v2/manage/* 404 | manage API 域名是 `swr-api.{region}`；swr.{region} 只是 registry 端点 |
| 工作负载 | Service 被 admission 拒（eip_type） | elb.autocreate 必须带 "eip_type":"5_bgp"（deploy.py 已修） |
| ELB | 白名单挂了，VPC 内机器仍能访问 | 正常现象：VPC 内访问 EIP 内网直达 Pod ENI，不经过监听器；公网方向才归白名单管。验证用外部独立 IP 测（预期 TCP 超时） |
| GPU | 显存按 48G 规划却 OOM | 实卡 NVIDIA L2 只有 24G（23034 MiB）；短视频峰值 18-20G 已贴上限，长视频开 SAM2_OFFLOAD_VIDEO_TO_CPU |

## 安全说明

- AK/SK、Token、SSH 私钥等凭证不应写入任何文件或日志
- 所有凭证仅通过环境变量或命令行参数传递
- `deploy.py` 用完集群证书临时文件后会自动删除；`token.json` 属敏感文件，部署完成后建议删除并轮换临时凭证
- 不要将 kubeconfig、业务视频、模型权重提交到代码仓库
