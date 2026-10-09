#!/usr/bin/env bash
set -euo pipefail

# =====================================================================
# kaniko 构建器镜像：构建（默认）或推送（--push）
#
#   ./build_or_push.sh            本地按平台构建，什么都不推
#   ./build_or_push.sh --push     推送这个版本**已经构建好的**镜像（不重新构建），
#                                 而且宁可失败也不"推一半"
#
# 仓库里最终只有**一条多架构 tag**（和历史上的 0.0.3 一样）：
#     docker.io/pyrominddynamics/kaniko-executor-pyromind:0.0.4   ← linux/amd64 + linux/arm64
# 所以推送分三步：先把两个单架构镜像当"原料"推上去 → 用它们合成多架构 tag →
# 再把那两个临时 tag 从仓库删掉。见 push 部分的长注释。
#
# 为什么合成一个脚本：版本号有三个地方必须一致 —— 镜像 tag、Dockerfile 的
# build-arg、build_sandbox.py 里的默认值，任何一处漂移都会让沙箱拉到拉不到
# 的 tag。所以就**只在下面声明一次**，再由本脚本负责传播：用它去构建，
# 推送成功后把 build_sandbox.py 里的默认值全部改成它。
#
# 凭据：本文件里**不写**任何账号密码，--push 时向你要（或你先 export）。
# =====================================================================

# ---------------------------------------------------------------------
# 版本号：只有这一处
# ---------------------------------------------------------------------
# ⚠️ 写中文提示时的坑：变量后面**紧跟中文标点**必须写成 ``${VAR}``。bash 会把
#    多字节字符算进变量名里（``$BUILD_VERSION（`` 会被当成变量名
#    ``BUILD_VERSION（``），在 ``set -u`` 下直接报 unbound variable。
BUILD_VERSION="${BUILD_VERSION:-0.0.4}"
IMAGE_NAME="${IMAGE_NAME:-kaniko-executor-pyromind}"

# ---------------------------------------------------------------------
# 目标仓库（推送用）
# ---------------------------------------------------------------------
DOCKER_HUB_HOST="${DOCKER_HUB_HOST:-docker.io}"
DOCKER_HUB_NS="${DOCKER_HUB_NS:-pyrominddynamics}"
# 删临时 tag 只能走 Hub 的 REST API（docker CLI 没有删远程 tag 的命令）。
DOCKER_HUB_API="${DOCKER_HUB_API:-https://hub.docker.com}"

# 凭据：**故意留空**，靠运行时输入（见 prompt_for_credentials）。
# 想把密码写进脚本之前请先想一下：这个文件是被 git 跟踪的，等于公开。
# CI 里就 export 这两个变量；密码要用 Docker Hub 的 access token，别用登录密码。
DOCKER_HUB_USER="${DOCKER_HUB_USER:-}"
DOCKER_HUB_PASSWORD="${DOCKER_HUB_PASSWORD:-}"

PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
PROXY="${PROXY:-http://127.0.0.1:7897}"

# ---------------------------------------------------------------------
# 派生变量
# ---------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

HUB_BASE="${DOCKER_HUB_HOST}/${DOCKER_HUB_NS}/${IMAGE_NAME}:${BUILD_VERSION}"
#: 两个单架构产物（本地构建的落点，也是合成多架构 tag 的"原料"）。
ARCH_TAGS=("${HUB_BASE}-amd64" "${HUB_BASE}-arm64")
#: 构建完成后本机应该有的全部 tag（缺一个就说明这个版本没建全）。
BUILD_TAGS=("${HUB_BASE}" "${ARCH_TAGS[@]}")

BUILD_SANDBOX_PY="${SCRIPT_DIR}/../../backend/build_sandbox.py"

usage() {
  cat <<'EOF'
用法: ./build_or_push.sh [--push]

  （不带参数）  按 PLATFORMS 逐个平台在本地构建 executor 镜像并打好 tag，
                不推送任何东西。
  --push, -p    推送已经构建好的 BUILD_VERSION 版本，**不会重新构建**。
                推送前会先逐个检查所有期望的 tag，只要缺一个就停下，
                绝不推一个残缺的集合。
                推完仓库里只会留一条**多架构** tag（amd64 + arm64）。
                账号密码运行时输入；也可以先 export：
                  export DOCKER_HUB_USER=xxx DOCKER_HUB_PASSWORD=xxx

环境变量覆盖: BUILD_VERSION, IMAGE_NAME, DOCKER_HUB_HOST, DOCKER_HUB_NS,
  DOCKER_HUB_USER, DOCKER_HUB_PASSWORD, DOCKER_HUB_API, PLATFORMS, PROXY
EOF
}

MODE="build"
for arg in "$@"; do
  case "$arg" in
    --push|-p) MODE="push" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

docker_ready() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

# 就地替换但不用 ``sed -i``（它的写法在 BSD 和 GNU 上不一样）。
replace_in_file() {
  local file="$1" old="$2" new="$3" tmp
  tmp="$(mktemp)"
  sed "s|${old}|${new}|g" "$file" > "$tmp" && mv "$tmp" "$file"
}

with_proxy() {
  HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY" "$@"
}

# 凭据从哪来：先看环境变量，没有就问。脚本里不留默认值。
# 非交互（CI、管道）时问不了，就直接报错并告诉你怎么给。
prompt_for_credentials() {
  local missing_env=()

  if [ -z "$DOCKER_HUB_USER" ]; then
    if [ -t 0 ]; then
      read -r -p "Docker Hub 用户名: " DOCKER_HUB_USER || true
    else
      missing_env+=("DOCKER_HUB_USER")
    fi
  fi

  if [ -z "$DOCKER_HUB_PASSWORD" ]; then
    if [ -t 0 ]; then
      read -r -s -p "Docker Hub 密码 / access token: " DOCKER_HUB_PASSWORD || true
      echo ""
    else
      missing_env+=("DOCKER_HUB_PASSWORD")
    fi
  fi

  if [ "${#missing_env[@]}" -gt 0 ]; then
    echo "错误：当前不是交互式终端，拿不到 Docker Hub 凭据。" >&2
    echo "      请先 export 这些变量再跑：" >&2
    for name in "${missing_env[@]}"; do
      echo "         export $name=..." >&2
    done
    echo "      （密码建议用 Docker Hub 的 access token，不要用登录密码）" >&2
    return 1
  fi

  if [ -z "$DOCKER_HUB_USER" ] || [ -z "$DOCKER_HUB_PASSWORD" ]; then
    echo "错误：用户名和密码都不能为空。" >&2
    return 1
  fi
}

# ---------------------------------------------------------------------
# 合成多架构 tag，并把两个临时 tag 删掉
# ---------------------------------------------------------------------

# 校验 :BUILD_VERSION 真的是多架构的（两条平台都在）。
# 只警告不终止：这时候镜像已经推上去了，报"失败"会误导人。
verify_manifest_list() {
  local out platforms
  out="$(with_proxy docker buildx imagetools inspect "$HUB_BASE" 2>&1 || true)"
  printf '%s\n' "$out"
  platforms="$(printf '%s' "$out" | grep -o 'linux/[a-z0-9_]*' | sort -u | tr '\n' ' ')"
  echo "   -> 平台: ${platforms:-（读不到）}"
  local want
  for want in linux/amd64 linux/arm64; do
    case " $platforms " in
      *" $want "*) ;;
      *)
        echo "   警告：${HUB_BASE} 里没看到 ${want}，集群在对应架构上会拉不到镜像。" >&2
        return 1
        ;;
    esac
  done
}

# JSON 字符串转义（密码里可能有 " 或 \）。
_json_escape() {
  printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

# 删掉那两个只是"原料"的单架构 tag，让仓库和历史上的 0.0.3 一样只剩一条多架构 tag。
# docker CLI 没有删远程 tag 的命令，只能走 Hub 的 REST API：
#   ① 用账号密码换一个 JWT；② DELETE /v2/repositories/<ns>/<repo>/tags/<tag>/
delete_arch_tags() {
  if ! command -v curl >/dev/null 2>&1; then
    echo "   警告：没有 curl，跳过删除临时 tag。" >&2
    return 1
  fi

  local jwt
  # 注意 ``|| true`` 必须在命令替换**里面**：管道 + ``set -o pipefail`` 下 curl
  # 一旦失败（断网、代理挂了），整个赋值会带着非零状态返回并让脚本直接退出 ——
  # 而这时候多架构 tag 其实已经推好了，不该让清理步骤把整件事带崩。
  jwt="$(with_proxy curl -sS -X POST "${DOCKER_HUB_API}/v2/users/login" \
      -H 'Content-Type: application/json' \
      --data "$(printf '{"username":"%s","password":"%s"}' \
        "$(_json_escape "$DOCKER_HUB_USER")" "$(_json_escape "$DOCKER_HUB_PASSWORD")")" \
      2>/dev/null |
    sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || true)"

  if [ -z "$jwt" ]; then
    echo "   警告：登录 Hub API 失败（账号开了两步验证？），没法自动删临时 tag。" >&2
    return 1
  fi

  local failed=0 tag name code
  for tag in "${ARCH_TAGS[@]}"; do
    name="${tag##*:}"
    code="$(with_proxy curl -sS -o /dev/null -w '%{http_code}' -X DELETE \
      -H "Authorization: JWT ${jwt}" \
      "${DOCKER_HUB_API}/v2/repositories/${DOCKER_HUB_NS}/${IMAGE_NAME}/tags/${name}/" 2>/dev/null || echo 000)"
    if [ "$code" = "204" ] || [ "$code" = "200" ]; then
      echo "   已删除临时 tag ${name}"
    else
      echo "   警告：删除 ${name} 失败（HTTP ${code}）" >&2
      failed=1
    fi
  done
  return "$failed"
}

# ---------------------------------------------------------------------
# 版本号回写
# ---------------------------------------------------------------------

# 让 build_sandbox.py 的默认值和刚发布的这个版本保持一致：SDK 里的默认镜像
# **就是**"构建沙箱该拉哪个 tag"的契约。两条默认值都要跟着走，少改一条就会有
# 一个集群拉到不存在的 tag（ImagePullBackOff）。
sync_version_to_code() {
  echo ""
  echo "把 BUILD_VERSION 同步进 build_sandbox.py..."
  if [ ! -f "$BUILD_SANDBOX_PY" ]; then
    echo "   跳过：找不到 $BUILD_SANDBOX_PY" >&2
    return 0
  fi

  # ① Docker Hub 那条：host、命名空间、镜像名、版本都在同一行，整条换掉
  local old
  old="$(grep -o "docker\.io/[^\"']*${IMAGE_NAME}:[0-9.]*" "$BUILD_SANDBOX_PY" | head -1 || true)"
  if [ -z "$old" ]; then
    echo "   警告：build_sandbox.py 里没找到 Docker Hub 默认值" >&2
  elif [ "$old" != "$HUB_BASE" ]; then
    replace_in_file "$BUILD_SANDBOX_PY" "$old" "$HUB_BASE"
    echo "   Docker Hub: $old"
    echo "            -> $HUB_BASE"
  else
    echo "   Docker Hub: 已经是 $HUB_BASE"
  fi

  # ② 上海（ACR）那条：**只换版本，host 不动**。
  #    host 是 VPC 内网地址（集群里的 daemon 要用它），和推镜像用的公网地址不是
  #    一回事，所以不能整条替换；但版本必须跟着走，否则上海会一直拉旧 executor。
  #    这条默认值在源码里跨两行（host 一行、"名字:版本" 一行），单行匹配只能锚
  #    "名字:版本"，取最后一个匹配 —— 那就是 ACR 那条。
  local acr_ref acr_old
  acr_ref="$(grep -o "${IMAGE_NAME}:[0-9.]*" "$BUILD_SANDBOX_PY" | tail -1 || true)"
  acr_old="${acr_ref##*:}"
  if [ -z "$acr_old" ] || [ "$acr_old" = "$acr_ref" ]; then
    echo "   警告：build_sandbox.py 里没找到上海 ACR 那条默认值" >&2
  elif [ "$acr_old" = "$BUILD_VERSION" ]; then
    echo "   上海 ACR: 已经是 ${BUILD_VERSION}"
  else
    replace_in_file "$BUILD_SANDBOX_PY" "${IMAGE_NAME}:${acr_old}" "${IMAGE_NAME}:${BUILD_VERSION}"
    echo "   上海 ACR: ${acr_old} -> ${BUILD_VERSION}（host 保持不变）"
  fi
}

# =====================================================================
# --push : 只推送（不重新构建）
# =====================================================================
if [ "$MODE" = "push" ]; then
  if ! docker_ready; then
    echo "Docker daemon 没在跑，请先启动 Docker Desktop" >&2
    exit 1
  fi

  echo "推送 ${IMAGE_NAME}:${BUILD_VERSION}（不重新构建）"
  echo "   Docker Hub: ${HUB_BASE}"
  echo "   平台:       ${PLATFORMS}"
  echo ""

  # 先确认本机真的构建过这个版本 —— 缺一个就整体不推
  MISSING=()
  for tag in "${BUILD_TAGS[@]}"; do
    docker image inspect "$tag" >/dev/null 2>&1 || MISSING+=("$tag")
  done

  if [ "${#MISSING[@]}" -gt 0 ]; then
    echo "错误：本机没有 ${BUILD_VERSION} 这个版本的镜像，什么都没推。" >&2
    echo "      缺失的 tag：" >&2
    for tag in "${MISSING[@]}"; do
      echo "         - $tag" >&2
    done
    echo "      要么这个版本从来没在本机构建过，要么构建完又被覆盖掉了。" >&2
    echo "      先构建，再推送：" >&2
    echo "         ./build_or_push.sh && ./build_or_push.sh --push" >&2
    exit 1
  fi

  # 凭据：环境变量优先，否则交互式问 —— 本文件里没有默认密码
  prompt_for_credentials

  echo "登录 ${DOCKER_HUB_HOST}，账号 ${DOCKER_HUB_USER}..."
  printf '%s' "$DOCKER_HUB_PASSWORD" |
    docker login "$DOCKER_HUB_HOST" -u "$DOCKER_HUB_USER" --password-stdin

  # ① 推两个单架构镜像当"原料"。
  #    imagetools create 只能引用**已经在 registry 里**的 manifest（官方文档明确
  #    写了这一点），所以必须先推这两个临时 tag。
  echo ""
  echo "推送单架构镜像（合成多架构 tag 的原料）..."
  for tag in "${ARCH_TAGS[@]}"; do
    echo "-> $tag"
    docker push "$tag"
  done

  # ② 合成多架构 tag —— 这才是集群真正拉的那一条。
  echo ""
  echo "合成多架构 tag ${HUB_BASE} ..."
  with_proxy docker buildx imagetools create -t "$HUB_BASE" "${ARCH_TAGS[@]}"

  echo ""
  echo "校验多架构 tag..."
  verify_manifest_list || true
  echo "https://hub.docker.com/r/${DOCKER_HUB_NS}/${IMAGE_NAME}/tags"

  # ③ 删掉那两个临时 tag，仓库里只留一条多架构 tag（和历史的 0.0.3 一致）。
  echo ""
  echo "清理临时 tag（仓库里只留 ${BUILD_VERSION} 这一条多架构 tag）..."
  if ! delete_arch_tags; then
    echo "   请手动到 https://hub.docker.com/r/${DOCKER_HUB_NS}/${IMAGE_NAME}/tags"
    echo "   删掉 ${BUILD_VERSION}-amd64 和 ${BUILD_VERSION}-arm64（它们只是原料，"
    echo "   删掉不影响 ${BUILD_VERSION} 那条多架构 tag）。"
  fi

  sync_version_to_code

  echo ""
  echo "已推送 ${IMAGE_NAME}:${BUILD_VERSION}（多架构：linux/amd64 + linux/arm64）"
  echo "提醒：Docker Hub 这条推好了；**上海 ACR 是另一条需要手动推的通道**，"
  echo "      记得把 ${BUILD_VERSION} 也推上去，否则上海集群拉不到这个 tag。"
  echo "注意：已安装的 SDK 里还是旧默认值，要用新版本还得重装："
  echo "        python -m build --wheel && pip install --no-deps --force-reinstall dist/*.whl"
  echo "        docker-rt --stop && docker-rt --daemon"
  exit 0
fi

# =====================================================================
# 默认 : 只构建
# =====================================================================
if ! docker_ready; then
  echo "Docker daemon 没在跑，请先启动 Docker Desktop" >&2
  exit 1
fi

CURRENT_CONTEXT=$(docker context show 2>/dev/null || echo "default")
if [ -n "$CURRENT_CONTEXT" ] && [ "$CURRENT_CONTEXT" != "default" ] && [ "$CURRENT_CONTEXT" != "desktop-linux" ]; then
  echo "当前 Docker context 是 '$CURRENT_CONTEXT'，切回 default"
  docker context use default >/dev/null 2>&1 || true
fi

IFS=',' read -r -a TARGET_PLATFORMS <<< "$PLATFORMS"
if [ "${#TARGET_PLATFORMS[@]}" -eq 0 ]; then
  echo "PLATFORMS 至少要有一个平台" >&2
  exit 1
fi

BUILDER_NAME="pyromind-builder"
if ! docker buildx inspect "$BUILDER_NAME" >/dev/null 2>&1; then
  echo "创建 builder: $BUILDER_NAME"
  docker buildx create --name "$BUILDER_NAME" --driver docker-container --use
else
  docker buildx use "$BUILDER_NAME"
fi

echo "在本地构建 kaniko executor 镜像..."
echo "   版本:       $BUILD_VERSION"
echo "   平台:       $PLATFORMS"
echo "   Docker Hub: $HUB_BASE"
echo "   构建目录:   $SCRIPT_DIR"
if [ -n "$PROXY" ]; then
  echo "   代理:       $PROXY"
fi
echo ""

# 每个平台单独 build 再 --load 进本地 daemon：docker 存不了多架构镜像，
# 多架构那条是推送时用 buildx imagetools 合成的（见 --push）。
PRIMARY_IMAGE=""
for PLATFORM in "${TARGET_PLATFORMS[@]}"; do
  case "$PLATFORM" in
    linux/amd64)
      LOCAL_IMAGE="${HUB_BASE}-amd64"
      PRIMARY_IMAGE="$HUB_BASE"
      ;;
    linux/arm64)
      LOCAL_IMAGE="${HUB_BASE}-arm64"
      ;;
    *)
      echo "不支持的平台: $PLATFORM" >&2
      echo "支持: linux/amd64, linux/arm64" >&2
      exit 1
      ;;
  esac

  echo "构建 $PLATFORM -> $LOCAL_IMAGE"
  with_proxy docker buildx build \
    --platform "$PLATFORM" \
    --build-arg BUILD_VERSION="$BUILD_VERSION" \
    --tag "$LOCAL_IMAGE" \
    --load \
    .

  # 顺手给 amd64 那份再打一个无后缀的本地 tag，方便直接 docker run 冒烟
  # （它**不是**要推到仓库的那条 —— 那条是多架构 manifest，只在推送时产生）。
  if [ -n "$PRIMARY_IMAGE" ]; then
    docker tag "$LOCAL_IMAGE" "$PRIMARY_IMAGE"
    echo "已打本地 tag ${PRIMARY_IMAGE}（仅本地冒烟用）"
  fi
  echo ""
done

echo "本机可用的镜像（都只是本地产物）:"
docker images "${DOCKER_HUB_HOST}/${DOCKER_HUB_NS}/${IMAGE_NAME}" \
  --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}'

echo ""
echo "构建完成（只在本地，没有推送）"
echo "   仓库里最终会是**一条多架构 tag**，由 --push 合成："
echo "   ${HUB_BASE}   (linux/amd64 + linux/arm64)"
echo ""
echo "下一步:"
echo "   ./build_or_push.sh --push    把 ${BUILD_VERSION} 推到 ${DOCKER_HUB_HOST}"
