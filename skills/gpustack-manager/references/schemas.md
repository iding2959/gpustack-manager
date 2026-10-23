# GPUStack v2.2.3 核心 schema 与枚举（schemas）

**何时读这个文件**：拿到响应 JSON 要解释字段含义、判断字段必填/可空、或需要某个枚举的合法取值时。
覆盖 ModelPublic / ModelCreate / ModelUpdate / ModelInstancePublic / ModelInstanceCreate / WorkerPublic / GPUDevicePublic / ModelRoutePublic / ModelRouteTargetPublic，以及 17 个枚举 + 实例状态机。
来源标注：`[openapi]` = 从 `/tmp/gpustack202-openapi.json` 的 `components.schemas` 解析；`[实测]` = 真实 v2.2.3 实例（`<host>`）只读响应验证。
配套文件：`endpoints.md`（路径）、`pitfalls.md`（踩坑）、`curl.md`（片段）。

## 3. 核心 schema 摘要

### 3.1 ModelPublic（GET 返回，36 个字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `created_at` `updated_at` | int / str | 主键与名称、时间戳（required）`[openapi]` |
| `source` | `SourceEnum` | 模型来源，required |
| `huggingface_repo_id` / `huggingface_filename` | str\|null | `source=huggingface` 时用 |
| `model_scope_model_id` / `model_scope_file_path` | str\|null | `source=model_scope` 时用 |
| `local_path` | str\|null | `source=local_path` 时用；**必须是 worker 容器内可见路径** |
| `replicas` | int | 期望副本数（启停开关） |
| `ready_replicas` | int | 就绪副本数 |
| `backend` / `backend_version` | str\|null | 后端**显示名**（`vLLM`）与版本 |
| `backend_parameters` | array\|null | 后端参数；含引号/JSON 的值必须拆成 flag+值两个元素 |
| `gpu_selector` | `GPUSelector` | `{gpu_ids: [..], gpus_per_replica: int}` |
| `worker_selector` | object\|null | 按 label 选 worker |
| `placement_strategy` | `PlacementStrategyEnum` | `spread` / `binpack` |
| `cluster_id` | int\|null | 所属集群（**创建时必填**，见 `pitfalls.md` §5.3） |
| `image_name` `run_command` `env` | — | 自定义镜像/启动命令/环境变量 |
| `categories` | array\<str> | 分类标签（如 `["llm"]`） |
| `access_policy` | `AccessPolicyEnum` | `public` / `authed` / `allowed_principals` |
| `extended_kv_cache` `speculative_config` `lora_list` `distributable` `cpu_offloading` `distributed_inference_across_workers` `restart_on_error` `generic_proxy` | — | 高级特性开关 |
| `owner_principal_id` | int | 属主 |
| `has_stale_lora_instances` | bool\|null | LoRA 实例过期标记 |

> **注意：`ModelPublic` 没有 `state` 字段**（section 里也没有任何 state/state_message）。模型状态只能由 `replicas` vs `ready_replicas` 推导，或去查 `/v2/model-instances` 的 `state` `[openapi]` `[实测]`。

### 3.2 ModelCreate（33 字段）

= `ModelPublic` 去掉 `id/created_at/updated_at/has_stale_lora_instances`，**加上** `enable_model_route`（bool，创建时是否同时建路由）。**required 只有 `source` 和 `name`** `[openapi]`。

实测补充：`cluster_id` 在 schema 里可选，**但不传会 500**（`null value in column "cluster_id" violates not-null constraint`）`[实测]`。

### 3.3 ModelUpdate（32 字段）

= `ModelCreate` 去掉 `enable_model_route`。**required 是 `source` + `name`** `[openapi]`，但这只是 Pydantic 的必填列表——服务端还有一层**按 source 的交叉校验**（见 `pitfalls.md` §5.20）：`source=local_path` 时不带 `local_path` 会 422 `local_path must be provided when source is 'local_path'` `[实测]`。

所以启停/扩缩的最小 body 是 `name` + `source` + `replicas` + **该 source 的定位字段**：

```json
// source=local_path
{"name":"qwen35-9b-local","source":"local_path","local_path":"/mnt/models/Qwen3.5-9B","replicas":0}
// source=huggingface
{"name":"m","source":"huggingface","huggingface_repo_id":"Qwen/Qwen2.5-7B-Instruct","replicas":0}
// source=model_scope
{"name":"m","source":"model_scope","model_scope_model_id":"Qwen/Qwen2.5-7B-Instruct","replicas":0}
```

### 3.4 ModelInstancePublic（41 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `model_id` `model_name` `created_at` `updated_at` | — | 实例身份；`name` 形如 `<model>-<5位随机>` |
| `state` | `ModelInstanceStateEnum` | 实例状态（9 态，见 §4） |
| `state_message` | str\|null | 状态说明，调度失败原因写在这里 |
| `worker_id` `worker_name` `worker_ip` `worker_ifname` `worker_advertise_address` | — | 落在哪个 worker |
| `port` / `ports` | int / array | 推理端口（如 `40018`） |
| `pid` | int\|null | 推理进程 pid |
| `download_progress` | number\|null | 下载进度 0–100 |
| `resolved_path` | str\|null | 实际加载的模型路径 |
| `backend` / `backend_version` / `api_detected_backend_version` | str | 后端与实际探测到的版本 |
| `injected_backend_parameters` | array\|null | 系统自动注入的参数 |
| `computed_resource_claim` | `ComputedResourceClaim` | `{ram, vram:{vendor: bytes}, offload_layers, total_layers, tensor_split, is_unified_memory}` |
| `gpu_type` `gpu_indexes` `gpu_addresses` | — | 占用的卡型号/序号/PCI 地址 |
| `distributed_servers` | `DistributedServers` | 多机推理：`mode`（`delegated\|initialize_later\|run_first`）、`subordinate_workers` |
| `restart_count` `last_restart_time` | — | 重启次数与时间 |
| `draft_model_source` `draft_model_download_progress` `draft_model_resolved_path` | — | 草稿模型（投机解码） |
| `mounted_loras` | array\|null | 已挂载的 LoRA |
| `cluster_id` `owner_principal_id` | — | 归属 |

### 3.5 ModelInstanceCreate（38 字段）

= `ModelInstancePublic` 去掉 `id/created_at/updated_at`。**required 只有 `source`、`name`、`model_name`** `[openapi]`。日常不需要直接调用它——实例由模型 `replicas` 派生。

### 3.6 WorkerPublic（31 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `hostname` `ip` `ifname` `port` | — | 身份与监听地址（`name` 实测是容器主机名 `b09881df3af9`） |
| `state` | `WorkerStateEnum` | `ready` / `not_ready` / `unreachable` / `maintenance` …（9 态） |
| `state_message` | str\|null | 状态原因 |
| `status` | `WorkerStatus`\|null | 实时指标：`cpu{total,utilization_rate}`、`memory{total,used,utilization_rate}`、`swap`、`filesystem[]`、`os`、`kernel`、`uptime`、`gpu_devices[]`、`rpc_servers` |
| `maintenance` | `Maintenance` | `{enabled: bool, message: str}`，PUT 时改它即进入/退出维护 |
| `advertise_address` `proxy_address` | str\|null | worker 对外地址 |
| `labels` | object | 调度用的 label |
| `cluster_id` `worker_pool_id` | int\|null | 归属 |
| `worker_version` `heartbeat_time` `unreachable` | — | 版本、心跳、不可达标记 |
| `provider` `provider_config` `external_id` `ssh_key_id` `provision_progress` | — | 云主机 provisioning 相关 |
| `system_reserved` | `SystemReserved`\|null | 系统预留资源 |
| `me` | bool\|null | 是否当前 principal 的 worker |

> **实测：`state` 有值（`ready`）而 `status` 为 `null`** `[实测]`。CLI 展示 CPU/内存使用率前必须判 `status` 非空，否则会崩。

### 3.7 GPUDevicePublic（22 字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | **string** | 形如 `"<worker_name>:<vendor类型>:<index>"`，实测 `b09881df3af9:cuda:0`，**不是整数** |
| `worker_id` `worker_name` `worker_ip` `worker_ifname` `cluster_id` | — | 归属（required） |
| `vendor` `type` `name` | str\|null | 厂商/类型/型号（实测 `nvidia` / `cuda` / `NVIDIA L40`） |
| `index` / `device_index` / `device_chip_index` | int\|null | 单机序号 / 设备号 / 芯粒号 |
| `memory` | `MemoryInfo` | **`{total, used, allocated, utilization_rate, is_unified_memory}`** |
| `core` | `GPUCoreInfo` | `{total, utilization_rate}`（GPU 利用率） |
| `temperature` | number\|null | 温度 |
| `driver_version` `runtime_version` `compute_capability` `arch_family` | str\|null | 驱动/运行时/算力/架构 |
| `uuid` | str\|null | 卡 UUID |
| `network` | `GPUNetworkInfo`\|null | 带宽/互联信息 |

**`memory.used` ≠ `memory.allocated`** `[实测]`：某次快照里 `used=20767047680`（约 19.3 GiB，驱动层实际占用，会随模型加载变化）、`allocated=46374322176`（约 43.2 GiB，调度器认领的 vram claim，与 `/v2/dashboard` 里 `active_models[].resource_claim.vram` 完全一致，不随推理波动）。`allocated` 在 schema 里已标 `deprecated`，但 v2.2.3 仍然返回 `[openapi]`。展示显存时应按用途二选一：看真实占用用 `used`，看「被模型预定」用 `allocated`。

### 3.8 ModelRoutePublic / ModelRouteTargetPublic（决定 `/v1` 能看到什么）

路由不是可选装饰：**`/v1/models` 的内容来自 ModelRoute，不是 Model**（见 `pitfalls.md` §5.19）。

`ModelRoutePublic`（`GET /v2/model-routes` 的元素）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `created_at` `updated_at` | — | 路由身份；`name` 默认就是 `/v1` 里 `model` 可用的名字 |
| `targets` / `ready_targets` | int | 目标总数 / 就绪目标数；`ready_targets=0` 时 `/v1/models` 不会列出它 `[实测]` |
| `access_policy` | `AccessPolicyEnum` | 默认 `authed`；**不影响 `/v1` 是否列出**（见 `pitfalls.md` §5.19） |
| `created_model_id` | int\|null | 由哪个模型自动创建而来 |
| `effective_name` | str\|null | 实际对外名（叠加 target 的 `overridden_model_name` 后的结果） |
| `categories` `meta` `generic_proxy` `owner_principal_id` | — | 分类 / 元数据 / 通用代理 / 属主 |

`ModelRouteTargetPublic`（`GET /v2/model-route-targets` 的元素）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `route_id` `route_name` | — | 目标身份；`name` 形如 `<route>-<hash>` |
| `model_id` / `provider_id` | int\|null | 指向本地模型 / 外部 ModelProvider，二选一 |
| `weight` | int | 加权（默认 0，多目标负载均衡用） |
| `overridden_model_name` | str\|null | 覆盖对外模型名（同名多后端时区分） |
| `fallback_status_codes` | array\|null | 触发兜底的状态码 |
| `state` | `TargetStateEnum` | `active` / `unavailable`（模型停了就转 `unavailable`）`[实测]` |

写入用的请求体：

| 端点 | body | 关键点 |
|---|---|---|
| `POST /v2/model-routes` | `ModelRouteCreate` | **required 只有 `name`**；`targets` 是 `ModelRouteTargetUpdateItem` 数组，元素字段 `model_id` / `provider_id` / `weight` / `overridden_model_name` / `fallback_status_codes` / `id` |
| `PUT /v2/model-routes/{id}` | `ModelRouteUpdate` | required `name`；`targets` 传数组从语义上应替换目标集（**未实测**，保守做法是显式调 `add-targets` / 删单个 target） |
| `POST /v2/model-routes/{id}/add-targets` | **裸数组** `[{...}]` | 不是 `{"targets":[...]}`，元素同上 `[openapi]` |

最小可用示例（本地模型）`[实测]`：

```json
POST /v2/model-routes
{"name":"qwen35-9b-local","targets":[{"model_id":2,"weight":1}]}
```

## 4. 枚举

| 枚举 | 取值 | 用在哪 |
|---|---|---|
| `SourceEnum` | `huggingface`、`model_scope`、`local_path` | `ModelCreate/Update/Public`、`ModelFilePublic`。**v2.2.3 没有 `ollama_library`** `[openapi]` |
| `ModelStateFilterEnum` | `ready`、`not_ready`、`stopped` | 仅作 `GET /v2/models?state=` 的**过滤参数**，不是响应字段 `[openapi]` |
| `ModelInstanceStateEnum` | `initializing`、`pending`、`starting`、`running`、`scheduled`、`error`、`downloading`、`analyzing`、`unreachable` | 实例 `state` |
| `PlacementStrategyEnum` | `spread`（打散）、`binpack`（装箱） | 模型调度 |
| `AccessPolicyEnum` | `public`、`authed`、`allowed_principals` | 模型/路由可见性 |
| `WorkerStateEnum` | `not_ready`、`ready`、`unreachable`、`pending`、`provisioning`、`initializing`、`deleting`、`error`、`maintenance` | worker `state` |
| `ClusterStateEnum` | `pending`、`provisioning`、`provisioned`、`ready` | 集群 `state` |
| `ModelFileStateEnum` | `error`、`downloading`、`ready` | 模型文件下载状态 |
| `ModelInstanceProxyModeEnum` | `worker`、`direct`、`delegated`、`tunnel` | 实例访问代理模式 |
| `BenchmarkStateEnum` | `pending`、`queued`、`running`、`completed`、`stopped`、`error`、`unreachable` | 压测状态 |
| `TargetStateEnum` | `active`、`unavailable` | 路由目标状态 |
| `BackendSourceEnum` | `custom`、`built_in`、`community` | 后端来源 |
| `ParameterFormatEnum` | `space`、`equal` | 后端参数拼装格式（`--flag value` vs `--flag=value`） |
| `DistributedServerCoordinateModeEnum` | `delegated`、`initialize_later`、`run_first` | 多机推理协调模式 |
| `SpeculativeAlgorithmEnum` | `eagle3`、`mtp`、`ngram` | 投机解码算法 |
| `ManufacturerEnum` | `amd`、`ascend`、`cambricon`、`hygon`、`iluvatar`、`metax`、`mthreads`、`nvidia`、`thead`、`unknown` | GPU 厂商 |
| `AuthProviderEnum` | `Local`、`OIDC`、`SAML`、`CAS` | 登录方式 |

实例状态机（`ModelInstanceStateEnum` 的 description 原文）`[openapi]`：

```
PENDING → ANALYZING → SCHEDULED → INITIALIZING → DOWNLOADING → STARTING → RUNNING
   ↓ 任一环节失败 → ERROR →（调度器重试）→ 回到 SCHEDULED
   RUNNING 时 worker 失联 → UNREACHABLE
```

---

**统计**：本文件覆盖 10 个核心 schema 摘要 + 17 个枚举 + 1 张实例状态机。
