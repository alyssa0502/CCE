# video-matting CCE Turbo 部署参数模板

> 复制本模板填写后交给 Claude 执行。带 ★ 的为必填项；标"或 auto_create"的可授权自动创建。

## 一、认证信息（必填）

```
★ HWC_AK =                 # 华为云 Access Key ID，如 AKIDxxxxxxxxxx
★ HWC_SK =                 # 华为云 Secret Access Key（仅通过环境变量传递，勿写入文件）
★ HWC_DOMAIN_ID =          # 账号 ID（租户 ID），控制台"我的凭证"页可查
  HWC_IAM_USER_ID =        # IAM 用户 ID（子账号调用时必填）
  HWC_IAM_USERNAME =       # IAM 用户名（用密码换 Token 时必填）
  HWC_IAM_PASSWORD =       # IAM 用户密码（用密码换 Token 时必填，仅环境变量）
★ HWC_REGION =             # 区域代码，如 cn-east-3
★ HWC_PROJECT_ID =         # 项目 ID，控制台"我的凭证"页可查
```

## 二、网络信息（必填；已有填 ID，没有填 auto_create）

```
★ VPC_ID = auto_create     # 已有 VPC ID，或 auto_create
★ SUBNET_ID = auto_create  # 已有子网 ID（网络ID），或 auto_create
  ENI_SUBNET_ID =          # Turbo ENI 子网 ID；留空则复用 SUBNET_ID
  SECURITY_GROUP_ID = auto_create  # 已有安全组 ID，或 auto_create
★ AVAILABLE_ZONE =         # 可用区，如 cn-east-3a（API 创建节点不支持 random）
```

## 三、集群与节点配置

```
  CLUSTER_NAME = video-matting-cluster    # CCE 集群名称
  CLUSTER_VERSION = v1.36                 # 集群版本，留空用区域默认
  CLUSTER_FLAVOR = cce.s2.small           # 集群规格（s1=单控制节点，s2=三控制节点高可用）
  NODE_NAME = video-matting-node-01       # 节点名称
★ NODE_FLAVOR = pi5.4xlarge.4            # GPU 节点规格（cn-east-3 实测=1×L2 24G，AZ cn-east-3d）
  NODE_COUNT = 1                          # 节点数量（长视频建议 2）
  NODE_OS = EulerOS 2.9                   # 节点操作系统
  NODE_ROOT_VOLUME_SIZE = 100             # 系统盘 GB
  NODE_DATA_VOLUME_SIZE = 200             # 数据盘 GB
  NODE_VOLUME_TYPE = SAS                  # 磁盘类型：SAS/SSD/ESSD/GPSSD
★ SSH_KEY_PAIR =                          # SSH 密钥对名称（需预先在控制台创建，用于登录节点构建镜像）
  EIP_BANDWIDTH = 100                     # 节点 EIP 带宽（Mbps）
  SHM_SIZE = 32Gi                         # 容器 /dev/shm 大小
```

## 四、镜像与服务配置

```
★ SOURCE_PACKAGE_URL =        # video-matting 源码包下载地址
  IMAGE_NAME = video-matting  # 镜像名称
  IMAGE_TAG = local           # 镜像标签
  SWR_REGISTRY = auto_create  # SWR 仓库：auto_create（自动登录+推送）或已有地址
  SERVICE_PORT = 8001         # 服务端口
  GPU_CONCURRENCY_LIMIT = 1   # GPU 并发限制
  SAM2_COMPILE = false        # SAM2 编译开关（true 会显著增加预热时间）
  GPU_LIMIT = 1               # 工作负载申请 GPU 数量
  ELB_BANDWIDTH = 100         # ELB 公网带宽（Mbps）
```

## 五、OSS 配置（数据隔离铁律，必读）

```
★ OSS_API_DOMAIN = http://<GPU节点VPC IP>:8001   # 必须显式钉死为自有垫片地址！
  OBS_OWN_BUCKET = obs-mcn                        # 自有结果桶（cn-east-3）
```

> **铁律（2026-11-04 起）**：镜像内代码的 `OSS_API_DOMAIN` 默认值指向客户生产 OSS。
> 任何部署都**必须**显式设置该 env 为自有垫片地址（垫片伪造存储 API，上传落节点本地，
> 之后人工/脚本 V2 签名转传自有桶 obs-mcn）。**绝不使用客户域名、客户桶名、客户 AK——
> 不设置该 env 就等于把结果写进客户生产桶。** 数据集与输出结果只进 obs-mcn。

## 六、验证配置（可选）

```
  TEST_VIDEO_URL =            # 推理验证用的测试视频 URL（留空跳过推理验证）
  TEST_VIDEO_FILE =           # 本地测试视频文件路径（留空跳过上传验证）
```

---

### 填写示例（最小集）

```
HWC_AK=AKIDxxxx
HWC_SK=xxxx（环境变量）
HWC_DOMAIN_ID=0a1b2c3d4e5f...
HWC_REGION=cn-east-3
HWC_PROJECT_ID=0a1b2c3d4e5f...
VPC_ID=auto_create
SUBNET_ID=auto_create
AVAILABLE_ZONE=cn-east-3a
NODE_FLAVOR=pi5.4xlarge.4
SSH_KEY_PAIR=my-keypair
SOURCE_PACKAGE_URL=https://example.com/video-matting.zip
```
