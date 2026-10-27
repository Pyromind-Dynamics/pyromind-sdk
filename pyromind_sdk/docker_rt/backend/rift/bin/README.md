# rift-client binaries

`docker_rt` 默认在本目录查找：

```
rift-client-linux-amd64
rift-client-linux-arm64
rift-client.sha256
```

在**本仓库内**构建（源码在 `../client`，Go module `rift-client`）：

```bash
cd ../client && make build-linux   # 输出到 ../bin/ 并生成 rift-client.sha256
```

也可以把 `DOCKER_RT_RIFT_BIN_DIR` 指向任一包含这两个二进制的目录。二进制是
release 产物，不提交仓库（已在根 `.gitignore` 忽略）。

`../client/pb/` 是根据 `rift-gateway` 仓库的 `proto/rift.proto` 生成并同步过来的；
改 proto 时两边要一起更新。