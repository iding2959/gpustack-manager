# GPUStack 常用 curl / jq 片段（curl）

**何时读这个文件**：想直接手搓一个请求做验证、排查 CLI 行为、或需要从 OpenAPI 反查参数时。
所有片段都从环境变量取地址与密钥：`export GPUSTACK_BASE_URL=http://<host>`、`export GPUSTACK_API_KEY=<key>`；**本文件不出现任何明文密钥**，也不要把 key 写进脚本或文档。
配套文件：`endpoints.md`（路径）、`schemas.md`（字段）、`pitfalls.md`（坑；下面标了 §5.x 的片段要先看对应条目）。

## 6. 常用 curl 片段

```bash
# 变量（密钥只从环境变量取）
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

## 7. 从 OpenAPI 复现结论的 jq 命令

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

# 账号密码登录（首选鉴权）：换 gpustack_session 会话
# 注意是 form-encoded（OAuth2 password 形式），不是 JSON
curl -s -c cookies.txt -X POST "$GPUSTACK_BASE_URL/auth/login" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "username=$GPUSTACK_USERNAME" \
  --data-urlencode "password=$GPUSTACK_PASSWORD"

# 之后所有请求带 cookie（会话能看到全量资源，不受 owner principal 过滤）
curl -s -b cookies.txt "$GPUSTACK_BASE_URL/v2/models" | jq -r '.items[] | "\(.id)\t\(.name)\t\(.ready_replicas)/\(.replicas)"'
