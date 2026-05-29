#!/usr/bin/env bash
set -euo pipefail

# ko-tts VPS 初始化脚本 (幂等, 可重复运行)
# 目标机: Ubuntu 24.04 x64, 以 root 执行一次。
# 用法/验证/救援见同目录 SERVER_SETUP.md
#
# ⚠️ 本脚本最后会加固 SSH: 之后 root 不能再 SSH 登录, 只能用 key + deploy 用户。
#    运行时请保持当前 root 会话不退出, 按结尾提示先验证 deploy 登录再 exit。

DEPLOY_USER="deploy"
PROJECT_DIR="/opt/ko-tts"
export DEBIAN_FRONTEND=noninteractive

step() { echo; echo "===== $* ====="; }

if [[ "${EUID}" -ne 0 ]]; then
  echo "必须以 root 运行此脚本" >&2
  exit 1
fi

# --------------------------------------------------------------------------
step "1/8 系统更新 & 常用工具"
# --------------------------------------------------------------------------
apt-get update
apt-get -y \
  -o Dpkg::Options::="--force-confdef" \
  -o Dpkg::Options::="--force-confold" \
  upgrade
apt-get install -y \
  curl vim htop ufw fail2ban unattended-upgrades \
  ca-certificates gnupg \
  python3-systemd   # fail2ban 的 systemd backend 依赖, 见步骤 5

# --------------------------------------------------------------------------
step "2/8 创建部署用户 ${DEPLOY_USER} (sudo 免密 + 同步 SSH key)"
# --------------------------------------------------------------------------
if id "${DEPLOY_USER}" &>/dev/null; then
  echo "用户 ${DEPLOY_USER} 已存在, 跳过创建"
else
  useradd -m -s /bin/bash "${DEPLOY_USER}"
  echo "已创建用户 ${DEPLOY_USER}"
fi
usermod -aG sudo "${DEPLOY_USER}"

# 免密 sudo: 先 visudo 校验语法, 通过再原子落盘
SUDOERS_FILE="/etc/sudoers.d/${DEPLOY_USER}"
SUDOERS_TMP="$(mktemp)"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "${DEPLOY_USER}" > "${SUDOERS_TMP}"
if visudo -cf "${SUDOERS_TMP}"; then
  install -m 0440 "${SUDOERS_TMP}" "${SUDOERS_FILE}"
  echo "已写入 ${SUDOERS_FILE}"
else
  echo "!! sudoers 语法校验失败, 放弃写入" >&2
  rm -f "${SUDOERS_TMP}"
  exit 1
fi
rm -f "${SUDOERS_TMP}"

# 把 root 的 authorized_keys 复制给 deploy (加固 SSH 前必须先有这一步, 否则会锁死)
DEPLOY_HOME="/home/${DEPLOY_USER}"
if [[ -f /root/.ssh/authorized_keys ]]; then
  install -d -m 700 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" "${DEPLOY_HOME}/.ssh"
  install -m 600 -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" \
    /root/.ssh/authorized_keys "${DEPLOY_HOME}/.ssh/authorized_keys"
  echo "已同步 authorized_keys 到 ${DEPLOY_HOME}/.ssh/"
else
  echo "!! /root/.ssh/authorized_keys 不存在, deploy 将无法用 key 登录。" >&2
  echo "!! 中止以免加固 SSH 后被锁死。请先确认 root 能 key 登录。" >&2
  exit 1
fi

# --------------------------------------------------------------------------
step "3/8 安装 Docker (官方 apt 源) + compose plugin"
# --------------------------------------------------------------------------
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

ARCH="$(dpkg --print-architecture)"
# shellcheck disable=SC1091
. /etc/os-release
echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update
apt-get install -y \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

usermod -aG docker "${DEPLOY_USER}"
systemctl enable --now docker
docker --version
docker compose version

# --------------------------------------------------------------------------
step "4/8 配置 ufw 防火墙 (默认拒绝入站, 放行 22/80/443)"
# --------------------------------------------------------------------------
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp     # SSH
ufw allow 80/tcp     # HTTP  (Caddy / ACME)
ufw allow 443/tcp    # HTTPS
ufw --force enable   # --force: 非交互, 已启用时重复运行也不报错
ufw status verbose

# --------------------------------------------------------------------------
step "5/8 配置 fail2ban (sshd jail, maxretry=5, bantime=1h)"
# --------------------------------------------------------------------------
cat > /etc/fail2ban/jail.d/sshd.local <<'EOF'
[sshd]
enabled  = true
backend  = systemd
maxretry = 5
bantime  = 1h
EOF
systemctl enable fail2ban
systemctl restart fail2ban
fail2ban-client status sshd || true

# --------------------------------------------------------------------------
step "6/8 启用自动安全更新 (仅 security, 不自动重启)"
# --------------------------------------------------------------------------
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

# 显式锁定只装 security 源, 并关闭自动重启 (避免打断长任务)
cat > /etc/apt/apt.conf.d/52unattended-upgrades-local <<'EOF'
// ko-tts: 仅安装 security 更新 + 不自动重启
#clear Unattended-Upgrade::Allowed-Origins;
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
EOF
systemctl enable --now unattended-upgrades

# --------------------------------------------------------------------------
step "7/8 加固 SSH (drop-in: 禁 root 登录 / 禁密码 / 仅 key)"
# --------------------------------------------------------------------------
# 按要求: 改动前留备份。主配置即便不改也备份一份;已有 drop-in 也备份。
cp -a /etc/ssh/sshd_config "/etc/ssh/sshd_config.bak.$(date +%Y%m%d%H%M%S)"

HARDEN="/etc/ssh/sshd_config.d/99-hardening.conf"
HARDEN_BAK=""
if [[ -f "${HARDEN}" ]]; then
  HARDEN_BAK="${HARDEN}.bak.$(date +%Y%m%d%H%M%S)"
  cp -a "${HARDEN}" "${HARDEN_BAK}"
fi

cat > "${HARDEN}" <<'EOF'
# ko-tts SSH 加固 (由 deploy/setup-server.sh 写入)
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
EOF

# 校验语法; 不通过则回滚, 绝不 reload (保住当前会话)
if sshd -t; then
  echo "sshd -t 校验通过"
  if systemctl cat ssh.service >/dev/null 2>&1; then
    SSH_UNIT="ssh"
  else
    SSH_UNIT="sshd"
  fi
  systemctl reload "${SSH_UNIT}" || systemctl restart "${SSH_UNIT}"
  echo "已 reload ${SSH_UNIT} (当前已建立的会话不受影响)"
else
  echo "!! sshd -t 校验失败, 回滚加固配置, 不重启 SSH" >&2
  if [[ -n "${HARDEN_BAK}" ]]; then
    mv -f "${HARDEN_BAK}" "${HARDEN}"
    echo "已恢复之前的 ${HARDEN}" >&2
  else
    rm -f "${HARDEN}"
    echo "已删除新写入的 ${HARDEN}" >&2
  fi
  exit 1
fi

# --------------------------------------------------------------------------
step "8/8 创建项目目录 ${PROJECT_DIR} (owner=${DEPLOY_USER})"
# --------------------------------------------------------------------------
install -d -o "${DEPLOY_USER}" -g "${DEPLOY_USER}" -m 0755 "${PROJECT_DIR}"
ls -ld "${PROJECT_DIR}"

# --------------------------------------------------------------------------
cat <<EOF

===== 完成! 下一步 (重要, 先别关掉当前 root 会话) =====

SSH 已加固: root 不能再 SSH 登录, 只能用 key 以 ${DEPLOY_USER} 用户登录。
当前这个 root 会话是已建立的连接, reload 不会断, 先保留它做安全网。

1) 另开一个本地终端, 测试 deploy 登录 (用 ~/.ssh/id_ed25519, 应免密):
     ssh ${DEPLOY_USER}@149.28.149.67

2) 进去后确认免密 sudo:
     sudo whoami        # 期望输出: root

3) 上面两步都成功, 再回到这个 root 会话退出:
     exit

4) 万一 deploy 登录失败 —— 不要退出这个 root 会话! 在这里排查:
     ls -la /home/${DEPLOY_USER}/.ssh
     journalctl -u "\${SSH_UNIT:-ssh}" -n 50 --no-pager
   仍无法解决, 用 Vultr 网页 console 救援 (见 deploy/SERVER_SETUP.md)。
EOF
