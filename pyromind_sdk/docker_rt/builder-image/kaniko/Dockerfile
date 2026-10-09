#
# docker-rt 专用的 kaniko executor 镜像
#
# 为什么要自己构建：上游镜像在 gcr.io，部分集群（尤其是上海 / cn-east-1）
# 根本拉不到。所以要 mirror 到一个集群能拉的 registry，再把
# DOCKER_RT_BUILD_IMAGE 指过去。
#
# 为什么必须用 -debug 变体：executor 镜像的最后一个 stage 是 FROM scratch，
# 里面只有静态二进制 /kaniko/executor 和一个 CA bundle —— 没有 shell，也没有
# coreutils。而 k8s 的 CUSTOM sandbox 模板硬编码了 command: ["sleep", "infinity"]，
# 没有 shell 的镜像连启动都启动不了，更别说往里 exec 命令了。-debug 就是同一个
# 镜像加上 busybox（提供 sleep、cat、/bin/sh），正是 kaniko 官方为这种场景发的。
#
# 不要把它重打包进别的 base image：kaniko 官方明确说在非官方镜像里跑 executor
# 是不支持的（它会把目标 rootfs 解包到自己容器的根目录，所以 base image 会影响
# 行为）。本文件只在官方镜像之上加 label、以及可选地追加自定义 CA 证书。

ARG KANIKO_VERSION=v1.24.0
ARG KANIKO_IMAGE=gcr.io/kaniko-project/executor

# 本自定义镜像的版本（改动本文件时要往上加）
ARG BUILD_VERSION=0.0.4

# ---- 构建阶段 -----------------------------------------------------------
FROM ${KANIKO_IMAGE}:${KANIKO_VERSION}-debug

# FROM 之后要重新声明 ARG（本阶段才能用）
ARG KANIKO_VERSION
ARG BUILD_VERSION

LABEL maintainer="pyromind"
LABEL version="${BUILD_VERSION}"
LABEL description="Custom kaniko executor with Pyromind CA trust store"
LABEL kaniko.version="${KANIKO_VERSION}"

# kaniko 需要往自己容器的根目录写东西（它在那里解包 FROM 镜像），所以在非特权
# 容器里也要以 root 跑。官方镜像没有 USER 指令；这行把它显式写出来，将来某个
# tag 改了这一点会立刻暴露。
USER 0

# ---- 让工具集不再依赖 /bin 和 /busybox ----------------------
#
# 在真实沙箱里，"用裸名字调用 executor 自己的工具"被证明不可靠。背后有两个事实：
#
#   * ``/bin`` 不是我们的。kaniko 会把它正在构建的镜像解包到**本容器**的 ``/``
#     上 —— 它就是这么构建的 —— 所以 ``/bin`` 属于**目标**镜像，随 stage 变
#     （先是 Debian，然后 Alpine……），而且切 stage 时会有短暂的一刻根本不存在。
#     上游的 ``-debug`` 镜像在那里只建了一个条目：``/bin/sh``。
#   * 上游把 busybox 放在 ``/busybox`` 并加进了 ``PATH``，但在那个沙箱的 exec
#     会话里，裸 ``grep``/``cat``/``sh`` 依然报 "not found"。确切原因没查清，
#     所以这里不再依赖 ``/busybox`` 了。
#
# ``/kaniko`` 是 kaniko 自己那份忽略列表里始终会保留的**普通目录**，所以放在那里
# 的工具集在整个构建过程中都存在，不管 ``/`` 上是什么。做法：把 busybox 复制进去，
# 并放到 PATH 最前面。
#
# 两个要紧的细节：
#   * 复制二进制，而不是软链到 ``/busybox``：运行时那个路径要是没被填上，软链会悬空；
#   * 副本命名成 ``busybox.real``，因为 ``busybox --list`` 里包含 ``busybox``
#     自己，``ln -sf busybox …/busybox`` 会把二进制替换成一个自引用的软链。
# 末尾那道校验会在我们依赖的工具没落地时**让镜像构建直接失败**，
# 这样 busybox/applet 的变化不可能在运行时静默地退化成"进去什么都没有"。
RUN ["/bin/sh", "-c", "set -e; mkdir -p /kaniko/bin; cp /busybox/busybox /kaniko/bin/busybox.real; for a in $(/kaniko/bin/busybox.real --list); do ln -sf busybox.real /kaniko/bin/$a; done; for a in sh grep cat ls nc awk nslookup sleep tail; do [ -x \"/kaniko/bin/$a\" ] || { echo \"kaniko/bin: missing applet $a\" >&2; exit 1; }; done; /kaniko/bin/sh -c 'echo kaniko/bin ready'"]

# ``/kaniko/bin`` 放最前，裸 ``sh``/``cat``/``grep``/``nc`` 就会命中这份能活下来的
# 副本；上游自己那几条留着当兜底。
ENV PATH=/kaniko/bin:/usr/local/bin:/kaniko:/busybox

# 可选：加自定义 CA 证书
# 如果有自定义 CA，把它们放进 certs/ 目录，然后取消下面这行的注释：
# COPY certs/*.crt /kaniko/ssl/certs/
#
# base image 已经在下面这个位置带了标准 CA 证书：
# /kaniko/ssl/certs/ca-certificates.crt

# 上游的 entrypoint 保持不变：沙箱从不走它（模板会覆盖 command），
# 但直接 docker run 时行为应和上游一致。
ENTRYPOINT ["/kaniko/executor"]
