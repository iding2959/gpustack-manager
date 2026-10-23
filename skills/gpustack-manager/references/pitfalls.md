# GPUStack v2.2.3 踩坑清单（pitfalls）

**何时读这个文件**：调 API 出现 422/500/404、启停模型不生效、`/v1` 调不通、分页漏数据、日志行数对不上时，先读这里再改代码。
共 **20 条**（§5.1–§5.20），每条都标了 `[openapi]`（从 OpenAPI/schema 推出）或 `[实测]`（对真实 v2.2.3 实例 `<host>` 发的请求验证）。
配套文件：`endpoints.md`（路径与参数）、`schemas.md`（字段含义）、`curl.md`（可直接跑的片段）。

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

见 `schemas.md` §3.1：`ModelPublic` 无 `state`。CLI 的 `models list --state` 是在**查询参数**上做服务端过滤（`ModelStateFilterEnum`），不能用返回字段再筛一次；表格里的「状态」列应由 `replicas/ready_replicas` 推导或联查实例 `[openapi]` `[实测]`。

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

---

### 5.21 `/v2/models` 只返回「归属当前 principal」的行，admin 也不例外

v2.2.0 实测（实例只有 1 个用户 `admin`、所有 API key 的 `owner_principal_id` 都是 1）：

- 用该 key 新建一个 `replicas=0` 的模型 → **立刻出现在 `/v2/models`**（id=35，`owner_principal_id=1`），删掉即消失。
- 同一集群里另有 24 个模型（`dashboard?cluster_id=1` 的 `model_count`），`GET /v2/models/{id}` 扫 id 1..40 **除自己那条外全部 404**；`/v2/models` 无论加 `cluster_id`、`x-organization-id`（1..8）、`owner_principal_id`、`all=true` 都是 `total=0`。
- 这些模型在 `GET /v2/my-models` 里可见（26 条，`owner_principal_id=7`），`targets/ready_targets` 正常，`/v1` 调用正常。

结论：列表与详情都按 **owner principal 相等** 过滤，`is_admin=true` **不会**绕过；`owner_principal_id=7` 是另一类 principal（组织/system 级）持有的历史数据。加 org 头无效。`[实测]`

绕开方式（按推荐顺序）：

1. **改用账号密码登录**（首选）：`--username admin --password ...` → `POST /auth/login` 拿 `gpustack_session`，同一实例 `/v2/models` 立刻返回全部 24 条、`instances list` 23 条也全都在。CLI 已支持，且**同时给两种凭据时以账号密码为准**。
2. `dashboard --cluster-id <id>` + `my-models` + `model-files` 交叉看聚合与对外入口（只有 API key 时的替代视图）。
3. 拿 owner principal 对应凭据，或服务端查库（`select owner_principal_id, count(*) from models group by 1;`）。

### 5.22 `/v2/dashboard` 不带 `cluster_id` 可能全 0

该端点按「当前集群」统计，多集群实例上默认集群未必是有负载的那个（实测 3 集群实例上不带参数返回全 0，带 `cluster_id=1` 才拿到 3 worker / 20 GPU / 24 模型 / 23 实例）。CLI 提供 `dashboard --cluster-id N`。`[实测]`

### 5.23 v2.2.0 与 v2.2.3 的管理面基本一致

实测两版 OpenAPI：2.2.0 = 131 path，2.2.3 = 133 path，差异仅 `/auth/cas/login`、`/auth/cas/callback`，`/v2/models`、`/v2/dashboard` 的查询参数完全相同。同一套 CLI 可同时打两版，版本问题优先怀疑数据/权限，而不是接口不兼容。`[实测]`

### 5.24 自定义后端的镜像名由 backend_version 决定

实测 v2.2.1：`backend=bge-custom` + `backend_version=bge-m3-v1.1.0` → worker 解析镜像为 `docker.1ms.run/bge-m3:v1.1.0`，该 registry 下无此 tag，报 `Failed to pull image ... not found`，实例在 `starting`/`error` 之间反复重试（`restart_on_error=true`）。同样内容改用 `backend=vLLM` 部署成功。结论：用自定义后端前，先在目标 worker 上确认镜像存在；不确定就用内置后端。`[实测]`

### 5.25 模型文件登记的唯一键与失败态

`POST /v2/model-files` 的 `(local_path, worker_id)` 是唯一键：同路径同 worker → `409 AlreadyExists`；同路径换 worker → 允许；目标 worker 上路径不存在 → `state=error`、`state_message="Path does not exist: /mnt/models/xxx"`；成功 → `state=ready` 且自动回填 `size`（例：/mnt/models/bge-m3 = 4587311867 字节）。`[实测]`

### 5.26 指定卡部署的写法与核对

`gpu_selector={"gpu_ids":["gpu-worker-206:cuda:4"]}`（id 来自 `GET /v2/gpu-devices` 的 `id`）能精确钉卡：实测实例落到 `worker_name=gpu-worker-206`、`gpu_indexes=[4]`，跑完删除后该卡 `used` 立即回落到基线、无残留容器。只写 `gpus_per_replica` 则是“要一张卡”，具体哪张由调度决定。`[实测]`

**统计**：本文件覆盖 26 条坑（§5.1–§5.26），其中 §5.19（ModelRoute 决定 `/v1` 可见性）与 §5.20（PUT 定位字段）是上线前最容易踩、排查成本最高的两条。
