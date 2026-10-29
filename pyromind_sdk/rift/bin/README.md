# rift-client binaries

`docker_rt` 默认在本目录查找：

```
rift-client-linux-amd64
rift-client-linux-arm64
rift-client.sha256          # 两个二进制的 sha256
rift-client.version         # 本目录二进制内注入的版本（Ping 返回它）
rift-client.source.sha256   # 构建时的 Go 源码指纹
```

在**本仓库内**构建（源码在 `../client`，Go module `rift-client`）：

```bash
cd ../client && make build-linux   # 输出到 ../bin/，并生成上面三个清单文件
```

也可以把 `DOCKER_RT_RIFT_BIN_DIR` 指向任一包含这两个二进制（及清单）的目录。
二进制是 release 产物，不提交仓库（已在根 `.gitignore` 忽略）。

- `rift-client.version`：bootstrap 用它判断 Pod 里跑的是不是这份二进制；
- `rift-client.source.sha256`：构建 wheel/sdist 时重新计算，源码变了会先自动重建、
  没有 Go 工具链则直接失败，避免把旧二进制打进发行包。

`../client/pb/` 是根据 `rift-gateway` 仓库的 `proto/rift.proto` 生成并同步过来的；
改 proto 时两边要一起更新（`make check-proto` 会比对两份 proto 和两端生成物）。
