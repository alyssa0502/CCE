# video-matting CCE Turbo 部署 Skill 使用指导

> 对应 skill：`huawei-cloud-cce-deploy`（本仓库本目录即该 skill 完整内容：SKILL.md + scripts/ + assets/）
> 适用：在华为云 CCE Turbo 上端到端自动部署 video-matting 服务（Florence-2-Large + SAM2.1 视频主体分割）
> 最后实测固化：2026-11-03/04 主环境全流程；2026-11-05 三建环境（自由节点 + 无 NAT + 路线A）再验证

---

## 1. 这是什么

一个 Claude Code skill，通过华为云 API 自动完成：

```
IAM 认证 → 建网络(VPC/子网/安全组) → 建 CCE Turbo 集群 → 建 GPU 节点(pi5.4xlarge.4, L2 24G)
→ 装 gpu-beta 插件装驱动(路线A) → [复刻模式跳过镜像构建] → 部署工作负载(Deployment+LoadBalancer Service)
→ 健康检查 → 预热 → 推理/上传 E2E 验证 → 数据隔离垫片 + ELB 白名单收尾
```

- 全程 API 驱动（AK/SK 签名 + Token），不需要登录控制台。
- CCE 没有独立的 Deployment REST 接口，脚本会自动调 `clustercert` 拿证书再走集群原生 Kubernetes API。

## 2. 目录结构

```
huawei-cloud-cce-deploy/
├── SKILL.md                    ← 工作流定义（含复刻模式，skill 的唯一"说明书"）
├── scripts/
│   ├── auth.py                 ← IAM 认证：Token 获取 + SDK-HMAC-SHA256 签名
│   ├── deploy.py               ← 部署编排：create-network / create-cluster / create-node
│   │                              / create-workload / swr-login / wait-cluster / wait-node
│   ├── verify.py               ← health-check / warmup / test-inference / test-upload
│   └── elb_acl.py              ← ELB 公网监听器挂 IP 白名单（收尾必跑）
└── assets/
    ├── deploy_template.md      ← 部署参数模板（全流程模式让用户填写）
    ├── api_reference.md        ← 华为云 API 参考清单（请求体示例；API 字段疑问以它为准）
    ├── vm_workload.yaml        ← 已验证工作负载基准（Deployment+Service 全套固化配置）
    └── obs_shim.py             ← 数据隔离垫片（伪造存储 API，结果落节点本地）
```

依赖：Python 3 + `requests`（`pip install requests`）。Windows/Linux 均可。

## 3. 两种使用方式总览

| | 全流程模式 | 复刻模式（推荐） |
|---|---|---|
| 触发 | 给出 `assets/deploy_template.md` 全套参数 | 用户说"部署一套一样的/一毛一样的集群" |
| 镜像 | 需要源码包，节点构建 10G+ 镜像（阶段四） | **跳过阶段四**，直接用 SWR 正典镜像 |
| 需要确认 | 模板所有 ★ 必填项 | 只确认 2 项：集群名、白名单 IP |
| 耗时 | ~40-60 分钟（含构建） | ~25-40 分钟 |

**正典镜像**：`swr.cn-east-3.myhuaweicloud.com/mcn-poc/video-matting:20261104`
（隔离保险丝版：默认 OSS_API_DOMAIN=127.0.0.1:1、OSS_BUCKET=obs-mcn、insta360 引用清零，容器内 grep 已验证。旧 20261103 保留在 SWR 可回滚。CCE 同账号免密拉取。）

## 4. 复刻模式固化参数表

| 项 | 固化值 |
|---|---|
| 区域/项目 | cn-east-3（上海一）/ `ef13bf432f15434e92104390d62dc5bf` |
| 集群 | 名称默认 `video-matting-cluster-<日期>`，版本 v1.36，规格 cce.s2.small，Turbo + eni |
| 节点 | `pi5.4xlarge.4`（**实卡 NVIDIA L2 24GB**，16核64G，1卡/节点）；AZ 只在 **cn-east-3d**；OS = Huawei Cloud EulerOS 2.0 **公共镜像**（驱动走路线A）；sshKey = video-matting-key；系统盘 100G / 数据盘 200G SAS；EIP 100M |
| 双卡供给 | cn-east-3 **无 2 卡 L2 规格**（pi5.4xlarge.4=1卡；pi5.24xlarge.4=6卡 且 cn-east-3d 常态 sellout）。gpu:2 必然 Pending（k8s 不跨节点拼卡） |
| 工作负载 | `kubectl apply assets/vm_workload.yaml`，占位符仅 2 处：SWR 镜像地址、OSS_API_DOMAIN |
| 并发/编译 | GPU_CONCURRENCY_LIMIT=1、SAM2_COMPILE=false（24G 显存硬约束） |
| 公网入口 | 共享型 ELB → `scripts/elb_acl.py` 挂白名单（默认放行 111.119.234.6） |
| 数据 | 自有桶 obs-mcn（cn-east-3）；测试视频 `obs-mcn/video-matting-test/test_person.mp4` 的 V2 预签名 URL |

## 5. 数据隔离铁律（每次部署必须过的门）

> 镜像内代码的 `OSS_API_DOMAIN` **默认值指向客户生产 OSS**。不设此 env = 结果写进客户生产桶。

1. 节点上起垫片 `assets/obs_shim.py`（`OBS_SHIM_ADVERTISE_IP=<节点VPC IP>` **必带**，否则预签名 URL 是废的）；
2. Deployment env 显式 `OSS_API_DOMAIN=http://<节点VPC IP>:8001`；
3. 数据集与输出结果只进自有桶 `obs-mcn`（V2 签名 PUT；测试视频也用自有桶预签名 URL）；
4. 除垫片模拟的 API 形状外，部署流程不出现客户任何信息（域名/桶名/凭据）。

## 6. GPU 驱动 = 路线A（公共镜像 + gpu-beta 插件装驱动）

节点 OS 用公共镜像（无预装驱动），节点 Active 后装 gpu-beta 插件（界面安装等效）：

1. **驱动 URL 必须显式给**（自由节点 operator 生成的 NvidiaNodeConfiguration 为空 → installer 无 URL 直接 CrashLoop）：
   PUT `values.custom.nvidia_driver_download_url`。
   池节点 operator 会自动填华为桶 URL，默认值即可。
2. **`is_driver_from_nvidia` 语义**（踩过 SignatureDoesNotMatch 才懂）：
   - `false` = URL 被当**相对路径**拼到 `obs.{region}.myhuaweicloud.com/cce.{region}/cce-addon/GPU-driver/` 基路径后（自建桶中转必炸）；
   - `true` = **原样透传**进 DS env `NVIDIA_DRIVER_DOWNLOAD_URL`。自定义任何桶地址都要 `true`。
3. **驱动源两选**（都不需要 NAT，OBS 走内网）：
   - 华为 OBS 真源：`https://hgcs-drivers-{region}.obs.{region}.myhuaweicloud.com/release/driver/tesla/NVIDIA-Linux-x86_64-{版本}.run`（匿名可拉）；
   - 自有桶 `obs-mcn` 中转：V2 签名 PUT + 48h 预签名 GET（Windows 下载 NVIDIA CDN 后上传——Akamai 403 华为云 IP，节点直下必 403）。
4. **改插件 values = 卸了重装**：同版本 PUT 报 400 `unsupported upgrade type` → DELETE 等卸净（GET 404）+ POST（annotations `addon.install/type: install`）。重装后 helm 会把 coredns / everest-csi-controller / nvidia-operator 副本拉回 2 → 单节点集群必须重压 1。
5. **验证口径（装完必验）**：节点容量 `nvidia.com/gpu=1` + 节点侧 `cd /usr/local/nvidia/bin && ./nvidia-smi` + 容器侧 `cd /usr/bin && ./nvidia-smi`（device-plugin 镜像起临时 Pod 即可）。

## 7. 复刻模式执行流程（7 阶段映射）

```
阶段一 认证        python scripts/auth.py --ak ... --sk ... --domain-id ... --region cn-east-3 --project-id ... --output token.json
阶段二 网络+集群   python scripts/deploy.py create-network ...   （VPC/子网 auto_create）
                   ⚠ 建完子网立即改 DNS（见 §8 坑1）
                   python scripts/deploy.py create-cluster ... → wait-cluster（5~15 分钟到 Available）
阶段三 GPU 节点    python scripts/deploy.py create-node ... → wait-node（5~10 分钟到 Active）
阶段3.5 装驱动     gpu-beta 路线A（见 §6）
阶段四 镜像构建    【复刻模式跳过】直接用 SWR 正典镜像
阶段五 工作负载    python scripts/deploy.py create-workload --image <SWR地址> ... 
                   ⚠ --env OSS_API_DOMAIN=http://<节点VPC IP>:8001 必传
阶段六 健康检查    python scripts/verify.py health-check --host <ELB_IP> --port 8001（503=模型加载中，继续等）
阶段七 预热+验证   python scripts/verify.py warmup --timeout 90
                   python scripts/verify.py test-inference --video-url <预签名URL> --mode auto
                   python scripts/verify.py test-upload --file ./test.mp4 --mode auto
收尾三件事  ① 起垫片 + 确认 env 已钉死  ② elb_acl.py 挂白名单  ③ 交付说明（见 §11 已知限制）
```

## 8. 高频坑速查（实测踩过）

| # | 坑 | 处理 |
|---|---|---|
| 1 | **子网 DNS 死值**：CCE 默认子网 DNS `100.125.36.53/100.125.5.151` 在部分 VPC 主位不可达 → cce-agent 下载失败、节点永远卡 Installing | 建完子网立即 `PUT /v2.0/subnets/{neutron_subnet_id}` `{"subnet":{"dns_nameservers":["100.125.1.250","100.125.36.53"]}}`（neutron v2.0；v1 subnet 无 PUT；ENI 子网同改）。已卡节点：SSH 修 `/etc/resolv.conf` + nmcli 后重跑 `bash /var/lib/cloud/instance/scripts/runcmd`，~1 分钟 Active |
| 2 | **Installing 态节点 DELETE 403**（CCE.01403001） | Abnormal 态可删；DELETE 429 "Resource locked" → 轮询到 404=已删 |
| 3 | **kubectl logs/exec 超时** | SG 缺 apiserver→kubelet `tcp/10250` 入方向（放行 VPC 网段） |
| 4 | **EIP 复用换 host key** | `ssh-keygen -R` 后 accept-new |
| 5 | **SWR 拉取 401 anonymous**（default SA 没挂 imagePullSecrets） | `kubectl patch sa default --patch '{"imagePullSecrets":[{"name":"default-secret"}]}'` 后**删 Pod 重建**（存量 Pod 不补） |
| 6 | **Service 被 admission 拒：`invalid character '#'`** | elb.autocreate 注解必须是**单行合法 JSON**，注释绝不能写进 `>` 折叠块标量 |
| 7 | **Service 一直 Pending** | 缺 `kubernetes.io/elb.autocreate` 注解；`type:public` 必须带 `"eip_type":"5_bgp"` |
| 8 | **Pod Pending: Insufficient nvidia.com/gpu** | gpu:N 是整卡调度且不跨节点拼卡；cn-east-3 单节点最多 1 卡，要 2 卡只能等 6 卡规格补货 |
| 9 | **单 Pod 连续第二个请求必 OOM** | 代码泄漏（finally 缺 del+empty_cache），请求后仍占 17.58G；重启 Pod 恢复。已反馈客户修码，修复前按"一 Pod 一请求"运营 |
| 10 | **长视频 OOM** | 开 `SAM2_OFFLOAD_VIDEO_TO_CPU=true`（24G 单卡跑不了 >22G 显存的视频） |
| 11 | **SWR manage API 404** | manage 域名是 `swr-api.{region}`；`swr.{region}` 只是 registry 端点 |
| 12 | **SWR push 401** | 临时凭据 24h 过期，重新生成即可（层去重，重推很快） |
| 13 | **白名单挂了 VPC 内机器仍能访问** | 正常：VPC 内 hairpin 直达 Pod ENI 不经监听器；白名单只管公网方向 |
| 14 | **显存按 48G 规划却 OOM** | 实卡 L2 只有 24G（23034 MiB），短视频峰值 18-20G 已贴上限 |

## 9. 部署产出清单

| 文件 | 内容 | 备注 |
|---|---|---|
| token.json | IAM Token + project_id | 敏感，部署完删除 |
| network.json | VPC/子网/安全组 ID | |
| cluster.json | 集群 ID/endpoint | |
| node.json | 节点 ID/公网 IP/私网 IP | 私网 IP = 垫片地址 |
| workload.json | Deployment/Service/ELB IP | |
| verify_result.json | 健康/预热/推理验证结果 | |

交付汇报：集群信息、节点信息（GPU 型号/数量）、服务地址（ELB IP:8001）、验证结果、配置文件位置。

## 10. 安全注意事项

- AK/SK、Token、SSH 私钥不写文件/日志；只走环境变量或命令行参数。
- `deploy.py` 用完集群证书自动删除临时文件。
- 垫片 8001 端口的 SG 规则**只放 VPC 网段**（无鉴权上传口绝不露公网）。
- `token.json` 部署完成后删除并轮换临时凭证。
- kubeconfig、业务视频、模型权重不进代码仓库。

## 11. 交付时必须说明的已知限制

1. **单 Pod 一请求**：连续第二个请求必 CUDA OOM（代码层 finally 缺 `del` + `torch.cuda.empty_cache()`），重启 Pod 恢复；已反馈客户修码，修复前按一 Pod 一请求运营。
2. **长视频**：需开 `SAM2_OFFLOAD_VIDEO_TO_CPU=true`（24G 单卡跑不了大视频）。
3. **双卡不可行**：代码是单卡逻辑（第二张卡可见但闲置），且 cn-east-3 无 2 卡 L2 规格。
4. **VPC 内访问不经监听器**：白名单对 VPC 内 hairpin 流量无效，属正常现象。
