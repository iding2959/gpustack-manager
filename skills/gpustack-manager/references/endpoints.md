# GPUStack v2.2.3 端点地图（endpoints）

**何时读这个文件**：要确认某个能力的 HTTP 方法/路径/查询参数，或判断某个接口属于哪个 tag 时。
覆盖 **33 个 tag + 1 组无 tag = 34 组 / 190 个 operation**，与 v2.2.3 全量 OpenAPI 一致。
来源标注：`[openapi]` = 从 `/tmp/gpustack202-openapi.json`（OpenAPI 3.1.0，`info.version = v2.2.3`，133 path / 190 operation）解析；`[实测]` = 对真实 v2.2.3 实例（下文以 `<host>` 代指）发只读请求验证。
配套文件：`schemas.md`（字段含义）、`pitfalls.md`（踩坑）、`curl.md`（可直接跑的片段）。

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

> **路由决定 `/v1` 可见性**：`/v1/models` 列的其实是「有 ModelRoute 且 target 就绪」的模型。`POST /v2/model-routes` 的 body 是 `ModelRouteCreate`（required 只有 `name`，`targets` 项支持 `model_id`/`provider_id`/`weight`/`overridden_model_name`）；`POST /v2/model-routes/{id}/add-targets` 的 body 是**裸数组**。详见 `schemas.md` §3.8 与 `pitfalls.md` §5.19。

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
| GET | `/v1/models` | OpenAI 形态模型列表（**与 `/v2/models` 不同源**，见 `pitfalls.md` §5.6） |
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
| POST | `/v1/rerank` | 重排（对接重排类模型） |

---

**统计**：本文件覆盖 **33 个 tag + 1 组无 tag 端点 = 34 组**，共 **190 个 operation**，与 v2.2.3 OpenAPI 全量一致。
