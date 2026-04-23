#!/usr/bin/env bash
# setup_privilege_env.sh — OpsAgent 最小权限环境一键初始化
#
# 用法：bash scripts/setup_privilege_env.sh
# 只需在脚本开头输入一次 sudo 密码，后续全部自动完成。
#
# 权限模型（方案2：三个独立组）：
#   每个脚本目录有专属组，仅含 ai-runner + 对应 ops-* 账号
#   目录权限 750，文件权限 640 — sudoers 路径约束完全保留

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}──${NC} $*"; }

RUNNER_USER="${OPSAGENT_USER:-$(whoami)}"
RUNNER_UID=$(id -u "$RUNNER_USER")
BASE_DIR="${OPSAGENT_BASE_DIR:-/var/lib/opsagent}"

echo "OpsAgent 最小权限环境初始化"
echo "主进程用户: ${RUNNER_USER} (uid=${RUNNER_UID})"
echo ""

echo "需要 sudo 权限完成系统配置，请输入密码："
sudo -v || fail "sudo 认证失败"

( while true; do sudo -n true; sleep 50; done ) &
SUDO_KEEPALIVE_PID=$!
trap 'kill $SUDO_KEEPALIVE_PID 2>/dev/null; exit' EXIT INT TERM

# ── Step 1：创建受限账号 ──────────────────────────────────────────────────────
step "创建受限系统账号"

create_user() {
    local name=$1 uid=$2
    if id "$name" &>/dev/null; then
        warn "账号已存在，删除重建: ${name}"
        sudo userdel "$name" 2>/dev/null || true
    fi
    sudo useradd -r -u "$uid" -s /sbin/nologin -M "$name"
    ok "创建账号: ${name} (uid=${uid})"
}

create_user ops-reader  9001
create_user ops-file    9002
create_user ops-service 9003

# ── Step 2：创建三个独立访问组 ────────────────────────────────────────────────
# 每组仅含 ai-runner + 对应 ops-* 账号，三个账号互相不可见
step "创建脚本目录访问组"

create_group() {
    local grp=$1 member=$2
    if getent group "$grp" &>/dev/null; then
        sudo groupdel "$grp" 2>/dev/null || true
    fi
    sudo groupadd --system "$grp"
    # sudo usermod -aG "$grp" "$RUNNER_USER"
    sudo usermod -aG "$grp" "$member"
    ok "创建组: ${grp}  成员: ${RUNNER_USER}, ${member}"
}

create_group opsagent-read    ops-reader
create_group opsagent-file    ops-file
create_group opsagent-service ops-service

# ── Step 3：创建脚本目录 ──────────────────────────────────────────────────────
step "创建脚本目录 /var/lib/opsagent/"

sudo mkdir -p "${BASE_DIR}"/{read,file,service}_scripts

sudo chown root:root "${BASE_DIR}"
sudo chmod 711 "${BASE_DIR}"
ok "根目录: ${BASE_DIR} (711, root:root)"

declare -A DIR_GROUP=(
    [read_scripts]=opsagent-read
    [file_scripts]=opsagent-file
    [service_scripts]=opsagent-service
)

for dir in read_scripts file_scripts service_scripts; do
    grp="${DIR_GROUP[$dir]}"
    sudo chown "${RUNNER_USER}:${grp}" "${BASE_DIR}/${dir}"
    sudo chmod 750 "${BASE_DIR}/${dir}"
    ok "子目录: ${BASE_DIR}/${dir}  (750, ${RUNNER_USER}:${grp})"
done

sudo chmod g+s /var/lib/opsagent/read_scripts
sudo chmod g+s /var/lib/opsagent/file_scripts   # 如果有其他目录也要加
sudo chmod g+s /var/lib/opsagent/service_scripts # 如果有其他目录也要加

# ── Step 4：写入 sudoers 规则 ─────────────────────────────────────────────────
step "配置 sudoers /etc/sudoers.d/opsagent"

SUDOERS_FILE=/etc/sudoers.d/opsagent
SUDOERS_CONTENT="${RUNNER_USER} ALL=(ops-reader)  NOPASSWD: /bin/bash ${BASE_DIR}/read_scripts/*
${RUNNER_USER} ALL=(ops-file)    NOPASSWD: /bin/bash ${BASE_DIR}/file_scripts/*
${RUNNER_USER} ALL=(ops-service) NOPASSWD: /bin/bash ${BASE_DIR}/service_scripts/*"

TMP_SUDOERS=$(mktemp)
echo "$SUDOERS_CONTENT" > "$TMP_SUDOERS"

if sudo visudo -c -f "$TMP_SUDOERS" &>/dev/null; then
    sudo cp "$TMP_SUDOERS" "$SUDOERS_FILE"
    sudo chmod 440 "$SUDOERS_FILE"
    rm -f "$TMP_SUDOERS"
    ok "sudoers 写入并验证通过: ${SUDOERS_FILE}"
else
    rm -f "$TMP_SUDOERS"
    fail "sudoers 语法验证失败，已中止（系统未被修改）"
fi

sudo visudo -c &>/dev/null && ok "visudo -c 全局语法验证通过" || fail "visudo -c 全局验证失败"

# ── Step 5：冒烟测试 ──────────────────────────────────────────────────────────
step "冒烟测试：验证脚本执行权限"

smoke_test() {
    local user=$1 dir_path=$2
    # 提取目录名（获取对应组）
    local dir_name=$(basename "$dir_path")
    local grp=${DIR_GROUP[$dir_name]}
    local script="${dir_path}/smoke_test.sh"

    # 1. 创建测试脚本（当前用户直接创建，无权限混乱）
    echo -e "#!/bin/bash\necho ok_${user}" > "$script"
    # 2. 关键：归属 运行用户 + 目录专属组，权限 750（符合最小权限）
    sudo chown "${RUNNER_USER}:${grp}" "$script"
    sudo chmod 750 "$script"

    # 3. 执行测试（捕获错误，不静默退出）
    set +e
    result=$(sudo -u "$user" /bin/bash "$script" 2>&1)
    exit_code=$?
    set -e

    # 清理
    rm -f "$script"

    # 校验结果
    if [[ $exit_code -eq 0 && "$result" == "ok_${user}" ]]; then
        ok "sudo -u ${user} 脚本执行正常"
    else
        fail "sudo -u ${user} 测试失败: ${result}"
    fi
}
smoke_test ops-reader  "${BASE_DIR}/read_scripts"
smoke_test ops-file    "${BASE_DIR}/file_scripts"
smoke_test ops-service "${BASE_DIR}/service_scripts"

# ── Step 6：打印汇总 ──────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo -e "${GREEN}  OpsAgent 最小权限环境初始化完成${NC}"
echo -e "${GREEN}══════════════════════════════════════════${NC}"
echo ""
echo "账号体系："
echo "  ops-reader  (uid=9001) — 只读命令"
echo "  ops-file    (uid=9002) — 文件操作"
echo "  ops-service (uid=9003) — 服务操作"
echo ""
echo "目录权限（750 + 专属组）："
echo "  ${BASE_DIR}/read_scripts/    (${RUNNER_USER}:opsagent-read)"
echo "  ${BASE_DIR}/file_scripts/    (${RUNNER_USER}:opsagent-file)"
echo "  ${BASE_DIR}/service_scripts/ (${RUNNER_USER}:opsagent-service)"
echo ""
echo "现在可以运行："
echo "  python main.py"
echo ""
