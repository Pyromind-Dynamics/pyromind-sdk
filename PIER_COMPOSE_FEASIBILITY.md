# pier（docker 环境）对 docker-rt 的能力评估 —— 已归档

> ⚠️ **本文已被 `COMPOSE_FEASIBILITY.md`（2026-10-20 建，2026-10-21 复核）取代。**
> compose / buildx / 网络 / logs / events 的全部评估结论请以主文档为准；本文不再维护，
> 仅保留 pier 产品线特有的实测记录备查。

## 为什么归档

1. 主文档是**命令级**拆解（20 个 compose 子命令 + compose file 字段矩阵 + 三层阻碍模型 +
   分期路线图），覆盖面与结构化程度均高于本文。
2. 本文有两处结论**与当前代码不符**，已在主文档修正：
   - "`NetworkStore.create(internal=…)` 会保存并在 inspect 回显" —— 当前
     `backend/networks.py` 无 `internal` 字段，`to_network_inspect` 硬编码
     `"Internal": False`（:156/:176）。
   - "`$DOCKER_RT_STATE_DIR/networks.json` 快照 + `restore_missing_for_containers()`
     跨机器自愈" —— 当前代码中两者均不存在（纯内存 NetworkStore，无落盘、无启动恢复）。
3. 本文的有效内容（buildx flag 级翻译表、healthcheck 自闭环方案、pier 实测记录）
   已全部并入主文档 §3.2 / §3.3 / §3.5b / §3.7。

## 仍有查阅价值的 pier 实测记录（2026-10-10）

- pier 容器内没有 docker wrapper（wrapper 是装进交互 shell 的），`docker compose build`
  直连 daemon API；compose v2 探测 BuildKit 失败（`/grpc` 404）后**自动回退经典 builder**，
  日志形态："Sending build context to Docker daemon 772B" → kaniko 接管。
- 当时暴露的真正缺陷：客户端按 gzip 发构建上下文、服务端按 tar 处理。**已修复**，
  回归测试 `pyromind_sdk/docker_rt/tests/test_build_sandbox.py:3004-3023`（772 B gzip 流场景）钉住。
- pier 的 `network_mode = "no-network"` 靠 squid 代理 sidecar 实现；"只连 internal 网络"
  在 k8s 的等价物是 NetworkPolicy，需 middleware 支持（主文档 §5 L3 / P2）。

## 解阻塞顺序（对 pier，已并入主文档 §6 路线图）

重装含 tar 修复的 wheel → 放开 wrapper 的 compose build 闸门 → healthcheck →
internal 隔离（提需求给 middleware）。**注意：主文档补充了一个前置判断——Cmd 不透传
（`pyromind_sdk_env.py:599-604`）不解决，放开 compose 闸门只会得到"up 成功但服务
全部跑 sleep infinity"的安静失败，因此 P0 是 Cmd 透传而非 wrapper 放行。**
