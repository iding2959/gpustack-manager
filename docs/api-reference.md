# GPUStack v2.2.3 API 参考（端点地图）

来源说明：`[openapi]` = 从 `/tmp/gpustack202-openapi.json`（OpenAPI **3.1.0**，`info.version = v2.2.3`，**133 个 path / 190 个 operation**）解析得出；`[实测]` = 对某 v2.2.3 实例（下文以 `<host>` 代指）发只读请求验证得出。

## 1. 概览

### 1.1 基本信息

| 项 | 值 | 来源 |
|---|---|---|
| 产品 / 版本 | GPUStack `v2.2.3`（`/version` 返回 `git_commit: 1cfcbbf`） | `[openapi]` `[实测]` |
| OpenAPI | 3.1.0，`servers` 为空（无 base 前缀，路径即 `/v2/...`） | `[openapi]` |
| 规模 | 133 path / 190 operation / 33 个 tag（另有 4 个 operation **无 tag**） | `[openapi]` |
| 管理面 | `/v2/*`（JSON，分页包裹） | `[openapi]` |
| 推理面 | `/v1/*`（OpenAI 兼容）、`/v1-openai/*`（旧别名）、`/v1/rerank` | `[openapi]` |
| 探针 | `/healthz` `/readyz` `/version`（无需鉴权；前两者返回 JSON 字符串 `"ok"`，后者返回版本对象） | `[openapi]` `[实测]` |

**base URL 不要带 `/v1`**：`--base-url http://host` 即可，路径里已含版本段。旧脚本用 `/v2/models` 前缀是对的。

### 1.2 鉴权

OpenAPI 声明了 4 种 security scheme，且**除探针与登录类端点外，所有 `/v2/*`、`/v1/*` 都同时接受这 4 种** `[openapi]`：

| scheme | 形式 | 实测 |
|---|---|---|
| `HTTPBearer` | `Authorization: Bearer <key>` | 200 ✅ |
| `APIKeyHeader` | `X-API-Key: <key>` | 200 ✅ |
| `HTTPBasic` | `Authorization: Basic base64(user:pass)` | 401 ❌（用 Web 管理账号密码被拒，需用 API key 语义） |
| `APIKeyCookie` | Cookie `gpustack_session`（Web 登录态） | 未验证 |

- 无 key / 错 key 均返回 `401 {"code":401,"reason":"Unauthorized","message":"Invalid authentication credentials"}` `[实测]`
- `/healthz` `/readyz` `/version` `/auth/config` 无需鉴权；`/v2/*`、`/v1/models` 需要 `[实测]`

### 1.3 分页约定

绝大多数 `/v2/*` 分页列表端点（27 个）共用 4 个查询参数 `[openapi]`：`page`（默认 **1**）、`perPage`（默认 **100**）、`watch`（默认 false）、`sort_by`。

```json
{"items": [ ... ], "pagination": {"page":1, "perPage":100, "total":123, "totalPage":2}}
```

- 包裹 schema 形如 `PaginatedList_ModelPublic_`：`items` + `pagination`，只有 `pagination` 是 required `[openapi]`
- **默认只回 100 条**：`GET /v2/model-sets` 返回 `total:123, totalPage:2`，必须翻页 `[实测]`
- `perPage` 服务端不设上限（`perPage=100000` 照收）`[实测]`；但 **`perPage=0` 直接 500**（`division by zero`）`[实测]`
- `page=0` 不报错，等同第 1 页 `[实测]`
- `watch=true` 是长轮询（服务端 hold 住连接，直到有变更），CLI 不要默认开 `[openapi]` `[实测]`（3 秒无响应）
- `sort_by`：**字段是每个端点各自的白名单**；白名单外的字段（例如到处都在用的 `-id`）会 422 并回一份允许列表 `[实测]`。倒序写法用 `-` 前缀，但**实测 `/v2/model-sets` 上 `name` 与 `-name` 返回完全相同的顺序（与不传也一样）**，说明该端点的 `sort_by` 实际被忽略——排序效果需要在有白名单的端点（`/v2/models` 等）上用多行数据另行验证，别假设它一定生效 `[实测]`。

| 端点 | 允许的 sort_by 字段 `[实测]` |
|---|---|
| `/v2/models` | `name, source, cluster_id, replicas, ready_replicas, created_at, updated_at` |
| `/v2/workers` | `name, state, ip, status.cpu.utilization_rate, status.memory.utilization_rate, gpus, created_at, updated_at` |
| `/v2/gpu-devices` | `name, index, cluster_id, worker_name, vendor, temperature, core.utilization_rate, memory.utilization_rate, created_at, updated_at` |
| `/v2/api-keys` | `name, expires_at, created_at, updated_at` |
| `/v2/clusters` | `name, provider, state, workers, ready_workers, gpus, models, created_at, updated_at` |
| `/v2/model-files` | `source, worker_id, state, resolved_paths, created_at, updated_at` |
| `/v2/users` | `username, is_admin, full_name, source, is_active, created_at, updated_at` |
| `/v2/benchmarks` | 名字/数据集/模型/state、各性能指标、`created_at, updated_at` |
| `/v2/model-instances` `/v2/model-routes` `/v2/model-sets` `/v2/model-providers` `/v2/model-route-targets` | 不校验（传 `-id` 不报错，但是否真的排序未验证——model-sets 实测无效果） |

### 1.4 响应包裹形态

| 场景 | 形态 | 来源 |
|---|---|---|
| 列表（主流） | `{"items":[...], "pagination":{...}}` | `[openapi]` `[实测]` |
| 列表（**无分页包裹**） | 只有 `{"items":[...]}`：`/v2/gpu-instance-types`、`/v2/inference-backends/list`、`/v2/dashboard/usage` | `[openapi]` `[实测]` |
| 单对象（GET/POST/PUT） | **裸对象**，无 `data`/`item` 包裹 | `[openapi]` `[实测]` |
| `/v2/models/{id}/instances` | 也是 `{items, pagination}`（不是裸数组） | `[实测]` |
| `/v2/inference-backends/all` | **裸数组** `[...]` | `[实测]` |
| `/healthz` `/readyz` | `application/json`，body 是 JSON 字符串 `"ok"`（不是对象） | `[实测]` |
| `/v2/model-instances/{id}/logs` | `text/plain; charset=utf-8` 流式文本，**不是 JSON** | `[实测]` |
| `*/dashboard`（worker / model / model-instance / cluster） | **HTTP 302**，`Location` 指向 Grafana | `[实测]` |
| `/v1/models` | OpenAI 形态 `{"object":"list","data":[]}` | `[openapi]` `[实测]` |

### 1.5 错误格式

统一 `ErrorResponse`：`{code:int, reason:str, message:str}`，三者都 required `[openapi]`。

| HTTP | 示例 `message` | 来源 |
|---|---|---|
| 400 | `Bad Request` | `[openapi]` |
| 401 | `Invalid authentication credentials` | `[实测]` |
| 403 | `Forbidden` | `[openapi]` |
| 404 | 措辞不统一：`Model not found` / `worker not found`（小写）/ `cluster 99999 not found`（带 id）/ `Model instance not found` / `GPU device not found` | `[实测]` |
| 409 | `Conflict`（同名资源） | `[openapi]` |
| 422 | `2 validation errors:\n  {'type': 'missing', 'loc': ('query', 'start_date'), ...}` | `[实测]` |
| 500 | `Unexpected error occurred: division by zero` | `[实测]` |
| 503 | `Service Unavailable` | `[openapi]` |

422 的 `message` 是 **Pydantic 原文多行字符串**（含 `loc`/`msg`），打印时保留换行更易读 `[实测]`。

## 2. 端点全表（按 tag）

> 表内路径省略 base URL。标注 `(302)` 的返回 Grafana 跳转，`(text)` 返回纯文本流。

### 2.1 Probes（3）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/healthz` | 存活探针，返回 JSON 字符串 `"ok"`，无需鉴权 |
| GET | `/readyz` | 就绪探针，返回 JSON 字符串 `"ok"`，无需鉴权 |
| GET | `/version` | `{"version":"v2.2.3","git_commit":"1cfcbbf"}`，无需鉴权 |

### 2.2 Models（8）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/models` | 列出模型；支持 `state`（`ready\|not_ready\|stopped`）、`search`、`categories`、`cluster_id`、`backend` 过滤；**没有 `name` 参数** |
| POST | `/v2/models` | 创建模型，body = `ModelCreate` |
| GET | `/v2/models/adapters` | LoRA 适配器列表；**`base` 为必填查询参数**，缺了 422 |
| GET | `/v2/models/{id}` | 单个模型 |
| PUT | `/v2/models/{id}` | 更新模型，body = `ModelUpdate`（必填 `source` + `name`）；改 `replicas` 即扩缩/启停 |
| DELETE | `/v2/models/{id}` | 删除模型（连带回收实例） |
| GET | `/v2/models/{id}/dashboard` | (302) Grafana 模型面板 |
| GET | `/v2/models/{id}/instances` | 该模型的实例列表（`{items,pagination}`） |

### 2.3 Model Instances（8）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/model-instances` | 列出实例；过滤 `id`、`model_id`、`worker_id`、`cluster_id`、`state`、`search` |
| POST | `/v2/model-instances` | 创建实例，body = `ModelInstanceCreate`（正常路径不手动建，由模型副本数派生） |
| GET | `/v2/model-instances/{id}` | 单个实例 |
| PUT | `/v2/model-instances/{id}` | 更新实例 |
| DELETE | `/v2/model-instances/{id}` | 删除实例（调度器会按 `replicas` 重建） |
| GET | `/v2/model-instances/{id}/dashboard` | (302) Grafana 实例面板 |
| GET | `/v2/model-instances/{id}/logs` | (text) 服务日志，流式；参数 `tail`、`follow`、`previous`、`worker_id`、`container_name` |
| GET | `/v2/model-instances/{id}/log-options` | 可选日志源（worker / 重启轮次 / 容器名），给 `logs` 拼参数用 |

### 2.4 Model Files（6）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/model-files` | 模型文件下载任务；过滤 `state`（`error\|downloading\|ready`）、`source`、`worker_id` |
| POST | `/v2/model-files` | 创建下载任务 |
| GET | `/v2/model-files/{id}` | 单个下载任务 |
| PUT | `/v2/model-files/{id}` | 更新 |
| DELETE | `/v2/model-files/{id}` | 删除（`cleanup_on_delete` 控制是否删磁盘文件） |
| POST | `/v2/model-files/{id}/reset` | 重置下载进度并重下 |

### 2.5 Workers（4）+ 无 tag 的 worker 端点（4）

**坑：`GET/POST /v2/workers`、`GET /v2/workers/{id}` 在 OpenAPI 里 `tags` 为空**，按 tag 遍历会漏掉这 3 个 `[openapi]` `[实测]`。

| 方法 | 路径 | 用途 | tag |
|---|---|---|---|
| GET | `/v2/workers` | 列出 worker；过滤 `name`、`search`、`uuid`、`me`、`cluster_id` | *(无)* |
| POST | `/v2/workers` | 创建 worker（手工注册） | *(无)* |
| GET | `/v2/workers/{id}` | 单个 worker | *(无)* |
| GET | `/v2/clusters/{id}/manifests` | 生成 worker 注册/部署清单 | *(无)* |
| PUT | `/v2/workers/{id}` | 更新 worker（含 `maintenance.enabled` 维护模式） | Workers |
| DELETE | `/v2/workers/{id}` | 删除 worker | Workers |
| GET | `/v2/workers/{id}/dashboard` | (302) Grafana worker 面板 | Workers |
| GET | `/v2/workers/{id}/privatekey` | 取 worker 的 SSH 私钥（敏感） | Workers |

### 2.6 GPU Devices（2）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/gpu-devices` | 列出 GPU 卡；过滤 `search`、`cluster_id` |
| GET | `/v2/gpu-devices/{id}` | 单张卡；`id` 是字符串，形如 `b09881df3af9:cuda:0`（`<worker_name>:<type>:<index>`） |

### 2.7 Clusters（9）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/clusters` | 列出集群（含聚合计数 `workers/ready_workers/gpus/models`、`is_default`） |
| POST | `/v2/clusters` | 创建集群（Docker / Kubernetes provider） |
| GET | `/v2/clusters/{id}` | 单个集群 |
| PUT | `/v2/clusters/{id}` | 更新集群 |
| DELETE | `/v2/clusters/{id}` | 删除集群 |
| POST | `/v2/clusters/{id}/set-default` | 设为默认集群 |
| POST | `/v2/clusters/{id}/worker-pools` | 在该集群下建 worker pool |
| GET | `/v2/clusters/{id}/registration-token` | 取 worker 注册 token |
| GET | `/v2/clusters/{id}/dashboard` | (302) Grafana 集群面板 |

### 2.8 Inference Backend（10）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/inference-backends` | 已启用后端（实测 4 个：`vLLM` `SGLang` `MindIE` `VoxBox`），分页 |
| GET | `/v2/inference-backends/list` | 后端配置总表（实测 5 个，多一个可自定义的 `Custom`），带 `versions/common_parameters/parameter_format`；**返回 `{items}`，没有 `pagination`** |
| GET | `/v2/inference-backends/all` | **全部**后端（实测 11 个，裸数组），含社区后端 |
| POST | `/v2/inference-backends` | 创建后端配置 |
| GET | `/v2/inference-backends/{id}` | 单个后端 |
| PUT | `/v2/inference-backends/{id}` | 更新后端 |
| DELETE | `/v2/inference-backends/{id}` | 删除后端 |
| GET | `/v2/inference-backends/backend_name/{backend_name}` | 按**显示名**取后端（`vLLM` 而非 `vllm`） |
| POST | `/v2/inference-backends/from-yaml` | 用 YAML 建后端 |
| PUT | `/v2/inference-backends/{id}/from-yaml` | 用 YAML 改后端 |

### 2.9 Model Routes（8）与 Model Route Targets（4）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/model-routes` | 列出模型路由 |
| POST | `/v2/model-routes` | 创建路由 |
| GET | `/v2/model-routes/{id}` | 单个路由 |
| PUT | `/v2/model-routes/{id}` | 更新路由 |
| DELETE | `/v2/model-routes/{id}` | 删除路由 |
| POST | `/v2/model-routes/{id}/add-targets` | 给路由加目标 |
| GET | `/v2/model-routes/{id}/access` | 路由的访问授权名单 |
| POST | `/v2/model-routes/{id}/access` | 新增访问授权 |
| GET | `/v2/model-route-targets` | 列出路由目标（`state`: `active\|unavailable`） |
| PUT | `/v2/model-route-targets/{id}` | 更新目标 |
| DELETE | `/v2/model-route-targets/{id}` | 删除目标 |
| POST | `/v2/model-route-targets/{id}/set-fallback` | 设为兜底目标 |

> **路由决定 `/v1` 可见性**：`/v1/models` 列的其实是「有 ModelRoute 且 target 就绪」的模型。`POST /v2/model-routes` 的 body 是 `ModelRouteCreate`（required 只有 `name`，`targets` 项支持 `model_id`/`provider_id`/`weight`/`overridden_model_name`）；`POST /v2/model-routes/{id}/add-targets` 的 body 是**裸数组**。详见 §3.8 与 §5.19。

### 2.10 Benchmarks（9）与 Benchmark Profiles（1）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/benchmarks` | 列出压测任务（含 rps/ttft/itl 等指标字段） |
| POST | `/v2/benchmarks` | 创建压测 |
| GET | `/v2/benchmarks/{id}` | 单个压测 |
| PUT | `/v2/benchmarks/{id}` | 更新压测 |
| DELETE | `/v2/benchmarks/{id}` | 删除压测 |
| PATCH | `/v2/benchmarks/{id}/state` | 改压测状态（`pending\|queued\|running\|completed\|stopped\|error\|unreachable`） |
| POST | `/v2/benchmarks/{id}/metrics` | 上报指标 |
| GET | `/v2/benchmarks/{id}/logs` | 压测日志（流式） |
| POST | `/v2/benchmarks/export` | 导出压测结果 |
| GET | `/v2/benchmark-profiles/default-config` | 默认压测档位（Throughput / Latency 等预设） |

### 2.11 Dashboard（3）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/dashboard` | 集群概览 JSON：`active_models`（含 `resource_claim.vram`）、`resource_counts`、`system_load.current/history` |
| GET | `/v2/dashboard/usage` | 用量列表（**返回 `{items}`，无 `pagination`**；实测空） |
| GET | `/v2/dashboard/usage/stats` | 用量统计聚合 |

### 2.12 Usage（8）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/usage/meta` | 可用指标/粒度/分组维度（`input_tokens`、`api_requests`、`day\|week\|month`…） |
| GET | `/v2/usage/summary` | **`start_date` + `end_date` 必填**（`YYYY-MM-DD`），否则 422 |
| POST | `/v2/usage/breakdown` | 用量明细分组 |
| POST | `/v2/usage/resource/breakdown` | 资源用量分组 |
| POST | `/v2/usage/gpu-instances/breakdown` | GPU 实例用量分组 |
| POST | `/v2/usage/storage/breakdown` | 存储用量分组 |
| GET | `/v2/usage/resource-events` | 资源事件流水（分页） |
| GET | `/v2/usage/resource/meta` | 资源维度元数据 |

`/v2/usage/summary` 实测返回：`{total_tokens, input_tokens, output_tokens, token_active_users, gpu_hours, instance_hours, storage_gb_days, active_users}` `[实测]`

### 2.13 API Keys（4）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/api-keys` | 列出 key；实测返回 `masked_value`（如 `"Custom API Key"`）、`value: null`、`scope: ["management","inference"]` |
| POST | `/v2/api-keys` | 创建 key（`value` 在 `ApiKeyPublic` 里可空，按惯例明文只在创建响应里出现，未实测） |
| PUT | `/v2/api-keys/{id}` | 更新 key |
| DELETE | `/v2/api-keys/{id}` | 删除 key |

### 2.14 Users（10）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/users/me` | 当前用户（实测 `{"username":"admin","is_admin":true,...}`） |
| PUT | `/v2/users/me` | 改自己的资料 |
| GET | `/v2/users` | 列出用户 |
| POST | `/v2/users` | 创建用户 |
| GET | `/v2/users/{id}` | 单个用户 |
| PUT | `/v2/users/{id}` | 更新用户 |
| DELETE | `/v2/users/{id}` | 删除用户 |
| PATCH | `/v2/users/{id}/activation` | 启用/停用 |
| GET | `/v2/users/{id}/memberships` | 所属组织/用户组 |
| GET | `/v2/user-directory` | 用户目录（选择器用） |

### 2.15 Auth（13）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/auth/config` | 登录方式探测 `{"external_auth":null,"is_oidc":false,"is_saml":false}`，无需鉴权 |
| POST | `/auth/login` | 本地账号登录（写 `gpustack_session` cookie） |
| POST | `/auth/logout` | 登出 |
| POST | `/auth/update-password` | 改密码 |
| GET | `/auth/oidc/login` | OIDC 跳转 |
| GET | `/auth/oidc/callback` | OIDC 回调 |
| GET | `/auth/saml/login` | SAML 跳转 |
| GET / POST | `/auth/saml/callback` | SAML 回调（OpenAPI 里同时声明了 GET 与 POST） |
| GET / POST | `/auth/saml/logout/callback` | SAML 登出回调（同上） |
| GET | `/auth/cas/login` | CAS 跳转 |
| GET | `/auth/cas/callback` | CAS 回调 |

### 2.16 My Models（2）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/my-models` | 当前 principal 可见的模型（受 access policy 过滤，实测为空 `total:0`） |
| GET | `/v2/my-models/{id}` | 单个可见模型 |

### 2.17 Model Sets（2）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/model-sets` | 模型目录（实测 `total:123`，**跨 2 页**）；含 `capabilities/categories/licenses/size` |
| GET | `/v2/model-sets/{id}/specs` | 单个模型集的可部署规格 |

### 2.18 Draft Models（1）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/draft-models` | 投机解码草稿模型候选（实测 4 条） |

### 2.19 Model Providers（9）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/model-providers` | 列外部模型提供商 |
| POST | `/v2/model-providers` | 创建提供商 |
| GET | `/v2/model-providers/{id}` | 单个提供商 |
| PUT | `/v2/model-providers/{id}` | 更新 |
| DELETE | `/v2/model-providers/{id}` | 删除 |
| POST | `/v2/model-providers/get-models` | 拉取（未保存的）提供商的模型列表 |
| POST | `/v2/model-providers/{id}/get-models` | 拉取已保存提供商的模型列表 |
| POST | `/v2/model-providers/test-model` | 用临时配置试调模型 |
| POST | `/v2/model-providers/{id}/test-model` | 用已保存配置试调 |

### 2.20 Model Evaluations（1）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v2/model-evaluations` | 发起模型评测（只有创建，没有列表/查询端点） |

### 2.21 GPU Instances 系列（7 + 5 + 5 + 5 + 5 + 1 = 28）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/gpu-instances` | 列出 GPU 云主机实例 |
| POST | `/v2/gpu-instances` | 创建 GPU 实例 |
| GET | `/v2/gpu-instances/{id}` | 单个实例 |
| PUT | `/v2/gpu-instances/{id}` | 更新实例 |
| DELETE | `/v2/gpu-instances/{id}` | 删除实例 |
| PUT | `/v2/gpu-instances/{id}/stop` | 停止实例（**独立端点**，与模型的 replicas=0 不同） |
| PUT | `/v2/gpu-instances/{id}/start` | 启动实例 |
| GET | `/v2/gpu-instance-types` | 可售规格；**返回 `{items}`，无 `pagination`**（实测空） |
| GET | `/v2/gpu-instance-templates` | 列出实例模板 |
| POST | `/v2/gpu-instance-templates` | 创建实例模板 |
| GET | `/v2/gpu-instance-templates/{id}` | 单个模板 |
| PUT | `/v2/gpu-instance-templates/{id}` | 更新模板 |
| DELETE | `/v2/gpu-instance-templates/{id}` | 删除模板 |
| GET | `/v2/gpu-instance-ssh-public-keys` | 列出 SSH 公钥 |
| POST | `/v2/gpu-instance-ssh-public-keys` | 新增 SSH 公钥 |
| GET | `/v2/gpu-instance-ssh-public-keys/{id}` | 单个 SSH 公钥 |
| PUT | `/v2/gpu-instance-ssh-public-keys/{id}` | 更新 SSH 公钥 |
| DELETE | `/v2/gpu-instance-ssh-public-keys/{id}` | 删除 SSH 公钥 |
| GET | `/v2/gpu-instance-persistent-volumes` | 列出持久卷 |
| POST | `/v2/gpu-instance-persistent-volumes` | 创建持久卷 |
| GET | `/v2/gpu-instance-persistent-volumes/{id}` | 单个持久卷 |
| PUT | `/v2/gpu-instance-persistent-volumes/{id}` | 更新持久卷 |
| DELETE | `/v2/gpu-instance-persistent-volumes/{id}` | 删除持久卷 |
| GET | `/v2/gpu-instance-persistent-volume-types` | 列出持久卷类型 |
| POST | `/v2/gpu-instance-persistent-volume-types` | 创建持久卷类型 |
| GET | `/v2/gpu-instance-persistent-volume-types/{id}` | 单个持久卷类型 |
| PUT | `/v2/gpu-instance-persistent-volume-types/{id}` | 更新持久卷类型 |
| DELETE | `/v2/gpu-instance-persistent-volume-types/{id}` | 删除持久卷类型 |

> 实测：实例/SSH key/持久卷/持久卷类型 `total:0`，但**模板有 19 条内置项**（如 `ubuntu-24-04`）；`/v2/gpu-instance-types` 无分页包裹 `[实测]`。这套能力属于云主机（K8s/云厂商）场景，Docker 集群上没有实例。

### 2.22 Cloud Credentials（6）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/cloud-credentials` | 列出云凭据 |
| POST | `/v2/cloud-credentials` | 创建云凭据 |
| GET | `/v2/cloud-credentials/{id}` | 单个凭据 |
| PUT | `/v2/cloud-credentials/{id}` | 更新 |
| DELETE | `/v2/cloud-credentials/{id}` | 删除 |
| GET | `/v2/cloud-credentials/{id}/provider-proxy/{path}` | 透传云厂商 API |

### 2.23 Worker Pools（4）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v2/worker-pools` | 列出 worker 池（实测空） |
| GET | `/v2/worker-pools/{id}` | 单个池 |
| PUT | `/v2/worker-pools/{id}` | 更新池 |
| DELETE | `/v2/worker-pools/{id}` | 删除池 |

> 创建池走 `POST /v2/clusters/{id}/worker-pools`（在 Clusters tag 下）`[openapi]`。

### 2.24 OpenAI-Compatible APIs（13）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/v1/models` | OpenAI 形态模型列表（**与 `/v2/models` 不同源**，见 §5.6） |
| POST | `/v1/chat/completions` | 对话补全（按 `model` 路由） |
| POST | `/v1/completions` | 文本补全 |
| POST | `/v1/responses` | Responses API |
| POST | `/v1/embeddings` | 向量化 |
| POST | `/v1/audio/transcriptions` | 语音转写 |
| POST | `/v1/audio/translations` | 语音翻译 |
| POST | `/v1/audio/speech` | TTS |
| POST | `/v1/images/generations` | 图像生成 |
| POST | `/v1/images/edits` | 图像编辑 |
| POST | `/v1/images/variations` | 图像变体 |
| POST | `/v1/moderations` | 内容审核 |
| POST | `/v1/score` | 打分 |

### 2.25 OpenAI-Compatible APIs (Legacy alias)（9）

旧前缀 `/v1-openai/*`，参数与 `/v1/*` 一致，仅少了 `audio/translations`、`images/variations`、`moderations`、`score`：

| 方法 | 路径 |
|---|---|
| GET | `/v1-openai/models` |
| POST | `/v1-openai/chat/completions` |
| POST | `/v1-openai/completions` |
| POST | `/v1-openai/responses` |
| POST | `/v1-openai/embeddings` |
| POST | `/v1-openai/audio/transcriptions` |
| POST | `/v1-openai/audio/speech` |
| POST | `/v1-openai/images/generations` |
| POST | `/v1-openai/images/edits` |

> 新代码一律用 `/v1/*`；`/v1-openai/*` 只是兼容别名 `[openapi]`。

### 2.26 Rerank（1）

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/v1/rerank` | 重排（`bge-reranker` 类模型用） |

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
| `cluster_id` | int\|null | 所属集群（**创建时必填**，见 §5.3） |
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

= `ModelCreate` 去掉 `enable_model_route`。**required 是 `source` + `name`** `[openapi]`，但这只是 Pydantic 的必填列表——服务端还有一层**按 source 的交叉校验**（见 §5.20）：`source=local_path` 时不带 `local_path` 会 422 `local_path must be provided when source is 'local_path'` `[实测]`。

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

路由不是可选装饰：**`/v1/models` 的内容来自 ModelRoute，不是 Model**（见 §5.19）。

`ModelRoutePublic`（`GET /v2/model-routes` 的元素）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` `name` `created_at` `updated_at` | — | 路由身份；`name` 默认就是 `/v1` 里 `model` 可用的名字 |
| `targets` / `ready_targets` | int | 目标总数 / 就绪目标数；`ready_targets=0` 时 `/v1/models` 不会列出它 `[实测]` |
| `access_policy` | `AccessPolicyEnum` | 默认 `authed`；**不影响 `/v1` 是否列出**（见 §5.19） |
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

## 5. 坑与注意

### 5.1 启停模型 = PUT 改 `replicas`，且必须回填 `source` + `name`

- 停止：`PUT /v2/models/{id}` body `{"name":..., "source":..., "replicas":0}` → 实例被回收、`ready_replicas` 归 0
- 启动/扩缩：同一端点 `replicas: N`
- **`ModelUpdate` 的 `required` 是 `source` + `name`**（`[openapi]` 的 required 列表），只发 `{"replicas":0}` 会 **422**；而只发 `name`+`source`+`replicas` **同样会 422**——还差该 source 的定位字段（`local_path` / `huggingface_repo_id` / `model_scope_model_id`），见 §5.20 `[实测]`
- 实例 id 会重建：旧实例被回收、新实例拿新 id（实测 2 → 3），查询实例要用 `model_id` 过滤而不是记旧 id `[实测]`

### 5.2 不要回写 GET 回来的整个对象

把 `GET /v2/models/{id}` 的结果原样 PUT 回去，会带上 `id/created_at/updated_at/ready_replicas/owner_principal_id/access_policy` 等只读或非预期字段，属于旧脚本的隐患 `[openapi]` `[实测]`。

**正确做法：只提交 `name` + `source` + 该 source 的定位字段 + 新的 `replicas`（见 §5.20）。**

### 5.3 `POST /v2/models` 必须显式带 `cluster_id`

schema 里 `cluster_id` 是可选，但不传直接 **500**（`null value in column "cluster_id" violates not-null constraint`）`[实测]`。CLI 应从 `GET /v2/clusters` 里取 `is_default=true` 的集群（实测 `id=1, name="Default Cluster"`）。

### 5.4 `backend` 传显示名，不传小写 id

`GET /v2/inference-backends` 实测返回 4 个：`vLLM`、`SGLang`、`MindIE`、`VoxBox`；`default_version` 为 `"0.25.1"`。模型创建时 `backend` 字段填这些**显示名**，填 `vllm` 后端会不匹配 `[实测]`。另有 `GET /v2/inference-backends/backend_name/{backend_name}` 按名取详情 `[openapi]`。

### 5.5 `GET /v2/workers` 没有 tag

`GET /v2/workers`、`POST /v2/workers`、`GET /v2/workers/{id}` 以及 `GET /v2/clusters/{id}/manifests` 在 OpenAPI 里 `tags` 为空（`[openapi]`）。按 tag 自动生成客户端/文档会漏掉这些，必须单独补。

### 5.6 `/v1/models` ≠ `/v2/models`

- `/v2/models` 是管理视图：全部模型、含未就绪/已停止的、返回完整对象 `[实测]`
- `/v1/models` 是 OpenAI 兼容视图：`{"object":"list","data":[...]}`，内容来自 **ModelRoute**（不是 Model 表），且要求 target 就绪。实测 `/v2/models` 有 1 个模型而 `/v1/models` 返回 `data: []`，建路由后才出现 `[实测]`
- 决定因素是路由而非 `access_policy`——详见 §5.19
- 做「模型是否可推理」判断时，两个都要看：v2 管理状态 + 路由/target 状态

### 5.7 `model-instances` 是派生资源

实例由模型 `replicas` 派生，**停模型不要直接 `DELETE /v2/model-instances/{id}`**：删了调度器会按 `replicas` 重建，而且实例 id 会变 `[实测]`（`replicas` 才是唯一事实来源）。

### 5.8 分页默认 100，必须翻页

`perPage` 默认 **100**。实测 `GET /v2/model-sets` 返回 `total:123, totalPage:2`，只取第一页会静默丢 23 条 `[实测]`。遍历时循环到 `page > totalPage`。

### 5.9 `perPage=0` 会 500

`GET /v2/models?perPage=0` → `500 {"code":500,"reason":"Internal Server Error","message":"Unexpected error occurred: division by zero"}` `[实测]`。做参数校验时把 `perPage >= 1` 挡在客户端。

### 5.10 `sort_by` 白名单每端点不同，且不保证排序生效

通用 `sort_by=-id` 在 `/v2/models`、`/v2/workers`、`/v2/gpu-devices`、`/v2/api-keys`、`/v2/clusters`、`/v2/users`、`/v2/model-files`、`/v2/benchmarks` 上都会 **422**（`Field 'id' is not sortable. Allowed fields: ...`）`[实测]`。

反过来，没有白名单校验的端点（如 `/v2/model-sets`）**不报错但也不排序**（`name`、`-name`、不传三者顺序一致）`[实测]`。结论：要保证顺序就自己排序，别指望服务端 `sort_by`。

### 5.11 `/v2/models` 没有 `name` 过滤参数

`GET /v2/models?name=zzz` 会**原样返回全部模型**（参数被忽略，不报错）`[实测]`。按名字解析 id 必须走 `?search=` 再客户端精确匹配，或先 `GET /v2/models?search=<name>` 后比对 `name`。

### 5.12 `*/dashboard` 是 302，不是 JSON

| 端点 | 实测结果 |
|---|---|
| `GET /v2/workers/{id}/dashboard` | `302 → /grafana/d/gpustack-worker/...` |
| `GET /v2/models/{id}/dashboard` | `302 → /grafana/d/gpustack-model/...` |
| `GET /v2/model-instances/{id}/dashboard` | `302 → /grafana/d/gpustack-model/...&var-model_instance_name=...` |
| `GET /v2/clusters/{id}/dashboard` | `302 → /grafana/d/gpustack-worker/...` |

OpenAPI 里这些端点的 `responses.200` 声明的是 JSON，**与实际不符** `[openapi]` `[实测]`。CLI 必须单独处理：不要 `-L` 跟着跳到 Grafana HTML，而是打印 `Location` 让用户浏览器打开。

### 5.13 `usage summary` 必填日期

`GET /v2/usage/summary` 缺 `start_date`/`end_date` → `422`，`message` 是 Pydantic 双错列表 `[实测]`。格式 `YYYY-MM-DD`。

### 5.14 实例日志是纯文本流

`GET /v2/model-instances/{id}/logs` 返回 `text/plain; charset=utf-8`。`follow=true` 时是 chunked 长连接，别用一次性 `json.loads` 去解析 `[实测]`。日志源要先看 `/log-options`（worker + 重启轮次 + 容器名）。

`tail` 的语义**不是行数**：实测 `tail=3` 返回约 30 行、`tail=200` 接近全量——它按「日志块」切分，多行块整体返回 `[实测]`。要精确行数只能客户端再截：`gpustack.py instances logs <id> | tail -n 20`。

### 5.15 `local_path` 必须在 worker 容器内可见

`source=local_path` 时路径是**worker 容器里的路径**，宿主机 `/mnt/models` 需要 `- /mnt/models:/mnt/models:ro` 挂进容器，否则模型会卡在 `scheduled`/`downloading` `[实测]`。

### 5.16 版本差异：`/v2/*` 是 v2.x 才有的管理面

- 本快照 v2.2.3 的管理面全部在 `/v2/*`，推理面在 `/v1/*`；更早的 GPUStack（v2.0 之前）只有 `/v1/*`——这条来自项目交接结论（`DESIGN-CONTRACT.md`），**本文未逐版本验证**（不在本文来源标注体系内）
- base URL **不要带 `/v1`**，也不要带 `/v2`，路径本身已含
- `/v1-openai/*` 是旧别名，新代码用 `/v1/*`
- 先用 `/version`（无需鉴权）拿版本，再决定走哪套路径

### 5.17 `state` 字段只存在于实例/worker，不在模型

见 §3.1：`ModelPublic` 无 `state`。CLI 的 `models list --state` 是在**查询参数**上做服务端过滤（`ModelStateFilterEnum`），不能用返回字段再筛一次；表格里的「状态」列应由 `replicas/ready_replicas` 推导或联查实例 `[openapi]` `[实测]`。

### 5.18 列表响应有三种包裹形态，别一律当分页

| 形态 | 端点 | 处理方式 |
|---|---|---|
| `{items, pagination}` | 绝大多数 `/v2/*` 列表 | 按 `pagination.totalPage` 翻页 |
| `{items}`（**无 pagination**） | `/v2/gpu-instance-types`、`/v2/inference-backends/list`、`/v2/dashboard/usage` | 直接取 `items`，没有翻页能力 |
| 裸数组 `[...]` | `/v2/inference-backends/all` | 直接 `length` |

三种都实测过 `[openapi]` `[实测]`。写通用客户端时先判 `has("items")` 再判 `pagination` 是否存在，否则 `r["pagination"]["totalPage"]` 会 KeyError。

### 5.19 模型 `ready` ≠ `/v1` 可推理：`/v1` 可见性由 ModelRoute 决定

- 实测：模型 `qwen35-9b-local` 为 `replicas=1 / ready_replicas=1`、实例 `state=running`，但 `GET /v1/models` 返回 `{"data":[],"object":"list"}`，`POST /v1/chat/completions` 返回

  ```json
  {"error":{"message":"Model not found","code":404,"type":"NotFound"}}
  ```

  `[实测]`
- 执行 `POST /v2/model-routes {"name":"qwen35-9b-local","targets":[{"model_id":2,"weight":1}]}` 成功后，`/v1/models` **立刻**列出该模型，chat 成功 `[实测]`
- 结论：**`/v1` 的模型可见性由 ModelRoute（+ target 就绪状态）决定，不是 Model 本身**；`access_policy` 不参与这个判定——实测模型与路由都是 `authed`，行为只随「有没有路由 / target 是否就绪」变化，把模型 ready 了也不会自动出现在 `/v1` `[实测]`
- 反向验证：模型停掉（`replicas=0`）后路由仍在，但 `targets=1 / ready_targets=0`、target `state=unavailable`，`/v1/models` **重新变空** `[实测]` → 双重条件：**有路由 + 有 ready target**
- 排障顺序：`GET /v2/model-routes`（**空列表 = 没有任何模型对外暴露**）→ 看 `targets` vs `ready_targets` → `GET /v2/model-route-targets?route_id=N` 看每个 target 的 `state` → 最后才查模型/实例状态
- 另注意：`/v1/*` 的错误是 **OpenAI 格式** `{"error":{"message","code","type"}}`，与 `/v2/*` 的 `{code,reason,message}` 不同，客户端要分开解析 `[实测]`

### 5.20 `PUT /v2/models/{id}` 必须按 `source` 携带定位字段

只发 `{"source":"local_path","name":"...","replicas":0}` 会 422：

```json
{"code":422,"reason":"Invalid","message":"... local_path must be provided when source is 'local_path'"}
```

`[实测]`。Pydantic 的 required 只有 `source`+`name`，但服务端还有一层按 source 的交叉校验 `[openapi]` `[实测]`：

| `source` | 必须一起带的定位字段 | 可选 |
|---|---|---|
| `local_path` | `local_path` | — |
| `huggingface` | `huggingface_repo_id` | `huggingface_filename` |
| `model_scope` | `model_scope_model_id` | `model_scope_file_path` |

正确做法：从 `GET /v2/models/{id}` 里**只取** `name` + `source` + 对应定位字段 + 新的 `replicas`，其余字段（`id`/`created_at`/`ready_replicas`/`state`…）不要回写（见 §5.2）。`[实测]`

## 6. 常用 curl 片段

```bash
# 变量
B=${GPUSTACK_BASE_URL:-http://<host>}
K=${GPUSTACK_API_KEY:?请先 export GPUSTACK_API_KEY}
H="Authorization: Bearer $K"

# 1) 健康三连（无需鉴权）
curl -s $B/healthz; echo
curl -s $B/readyz; echo
curl -s $B/version | jq .

# 2) 列出模型（显式给 perPage，避免默认 100 截断）
curl -s -H "$H" "$B/v2/models?perPage=100&sort_by=name" \
  | jq -r '.pagination, (.items[] | "\(.id)\t\(.name)\t\(.ready_replicas)/\(.replicas)\t\(.backend)\t\(.source)")'

# 3) 按名字解析 id（没有 name 参数，只有 search）
curl -s -H "$H" --get --data-urlencode "search=qwen35-9b-local" "$B/v2/models" \
  | jq -r '.items[] | select(.name=="qwen35-9b-local") | .id'

# 4) 停模型：replicas=0；必须回填 name+source **+ 该 source 的定位字段**（否则 422，见 §5.20）
curl -s -X PUT -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"qwen35-9b-local","source":"local_path","local_path":"/mnt/models/Qwen3.5-9B","replicas":0}' \
  "$B/v2/models/2" | jq '{id,name,replicas,ready_replicas}'

# 5) 启动/扩容（同样要带定位字段）
curl -s -X PUT -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"qwen35-9b-local","source":"local_path","local_path":"/mnt/models/Qwen3.5-9B","replicas":1}' \
  "$B/v2/models/2" | jq '{id,replicas,ready_replicas}'

# 6) 创建本地模型（cluster_id 必填；backend 用显示名）
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"qwen35-9b-local","source":"local_path","local_path":"/mnt/models/Qwen3.5-9B",
       "backend":"vLLM","backend_version":"0.25.1","replicas":1,
       "cluster_id":1,"gpu_selector":{"gpus_per_replica":1},
       "placement_strategy":"spread","categories":["llm"]}' \
  "$B/v2/models" | jq '{id,name,replicas}'

# 7) worker 状态（注意 status 可能为 null）
curl -s -H "$H" "$B/v2/workers?perPage=100" \
  | jq -r '.items[] | "\(.id)\t\(.name)\t\(.state)\tCPU=\(.status.cpu.utilization_rate // "-")%\tMEM=\(.status.memory.utilization_rate // "-")%"'

# 8) GPU 显存（distinguish used vs allocated）
curl -s -H "$H" "$B/v2/gpu-devices?perPage=100" \
  | jq -r '.items[] | "\(.worker_name)\tGPU\(.index)\t\(.name)\tvram \(.memory.used)/\(.memory.total)\talloc \(.memory.allocated)"'

# 9) 实例列表与日志
curl -s -H "$H" "$B/v2/model-instances?model_id=2" \
  | jq -r '.items[] | "\(.id)\t\(.name)\t\(.state)\t\(.worker_name):\(.port)\t\(.state_message // "")"'
curl -s -H "$H" "$B/v2/model-instances/3/logs?tail=100"

# 10) 翻页取全量
page=1; while :; do
  r=$(curl -s -H "$H" "$B/v2/model-sets?page=$page&perPage=100")
  echo "$r" | jq -r '.items[] | "\(.id)\t\(.name)"'
  tp=$(echo "$r" | jq -r '.pagination.totalPage')
  [ "$page" -ge "$tp" ] && break
  page=$((page+1))
done

# 11) dashboard 是 302：只取 Location，不要 -L
curl -s -o /dev/null -D - -H "$H" "$B/v2/workers/1/dashboard" | grep -i '^location:'

# 12) 用量汇总（日期必填）
curl -s -H "$H" "$B/v2/usage/summary?start_date=2026-10-01&end_date=2026-10-23" | jq .

# 12.5) 让模型对 /v1 可见：建 ModelRoute（模型 ready 也不等于 /v1 能调，见 §5.19）
curl -s -H "$H" "$B/v2/model-routes" | jq -r '.items[] | "\(.id)\t\(.name)\t\(.ready_targets)/\(.targets)"'  # 先看有没有路由
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"name":"qwen35-9b-local","targets":[{"model_id":2,"weight":1}]}' \
  "$B/v2/model-routes" | jq '{id,name,targets,ready_targets}'
curl -s -H "$H" "$B/v1/models" | jq -r '.data[].id'   # 路由生效后这里才有东西

# 13) OpenAI 兼容冒烟（注意用 /v1 与 v1 暴露的模型名；模型名来自 ModelRoute，不是 /v2/models 的 name）
curl -s -X POST -H "$H" -H 'Content-Type: application/json' \
  -d '{"model":"qwen35-9b-local","messages":[{"role":"user","content":"hi"}],"max_tokens":16}' \
  "$B/v1/chat/completions" | jq .
```

## 7. 复现本文结论所用的 jq 命令

```bash
F=/tmp/gpustack202-openapi.json

# 版本与规模
jq -r '.openapi, .info.version, (.paths|length)' $F

# 逐 tag 列端点（注意 tags 可能为空数组——worker 的 3 个端点就是这样）
jq -r '.paths as $p | [$p|to_entries[] as $x | $x.value|to_entries[]
        | select(.key|test("^(get|put|post|delete|patch)$"))
        | {tag:(.value.tags[0]//"(untagged)"), m:(.key|ascii_upcase), path:$x.key}]
       | group_by(.tag)[] | "### \(.[0].tag)", (.[]|"\(.m)\t\(.path)")' $F

# 枚举
jq -r '.components.schemas | to_entries[] | select(.key|test("Enum$"))
       | .key + " = " + (.value.enum|tojson)' $F

# 某端点的查询参数与请求体
jq -r '.paths["/v2/models"].get.parameters[]? | "\(.name)(\(.in)) req=\(.required//false)"' $F
jq -c '.paths["/v2/models/{id}"].put.requestBody.content["application/json"]' $F
```

---

**统计**：本文覆盖 **33 个 tag + 1 组无 tag 端点 = 34 组**，共 **190 个 operation**，与 OpenAPI 全量一致；核心 schema 摘要 10 个（含 ModelRoute / ModelRouteTarget），「坑与注意」20 条（§5.1–§5.20）。
