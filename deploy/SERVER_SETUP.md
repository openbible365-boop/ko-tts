# VPS 初始化 — setup-server.sh 操作手册

针对 `149.28.149.67`(Ubuntu 24.04, Vultr Singapore)。脚本幂等,可重复运行。

> ⚠️ **核心风险**:脚本第 7 步加固 SSH 后,**root 不能再 SSH 登录**,密码登录也被关闭,
> 之后只能用 `~/.ssh/id_ed25519` 以 **deploy** 用户登录。
> 全程**保持运行脚本的那个 root 会话不退出**,直到你在另一个终端验证 deploy 能登录 + sudo 可用。

---

## 1. 上传脚本到 VPS

本地项目根目录执行(此时 root 仍可登录):

```bash
scp -i ~/.ssh/id_ed25519 deploy/setup-server.sh root@149.28.149.67:/root/setup-server.sh
```

## 2. 执行

开一个 root 会话并运行(`tee` 同时存日志便于回看):

```bash
ssh -i ~/.ssh/id_ed25519 root@149.28.149.67
chmod +x /root/setup-server.sh
/root/setup-server.sh 2>&1 | tee /root/setup-server.log
```

**这个会话先别关。** 按脚本结尾提示,另开终端验证 deploy 登录成功后再 `exit`。

---

## 3. 逐步验证

每个大步骤都打印了 `===== N/8 ... =====`。下面是对应的独立验证命令(在 VPS 上跑)。

### 1/8 系统更新 & 工具
```bash
dpkg -l curl vim htop ufw fail2ban unattended-upgrades ca-certificates gnupg python3-systemd \
  | grep -E '^ii' | awk '{print $2}'
```
应列出全部 9 个包。

### 2/8 deploy 用户
```bash
id deploy                                  # 应含 groups=...(sudo),...(docker)
cat /etc/sudoers.d/deploy                   # deploy ALL=(ALL) NOPASSWD:ALL
sudo -lU deploy | grep -i nopasswd          # 确认免密 sudo 规则
ls -la /home/deploy/.ssh                    # 目录 700, authorized_keys 600, owner deploy
```

### 3/8 Docker
```bash
docker --version
docker compose version                      # 注意是 "compose" 子命令(plugin), 不是 docker-compose
systemctl is-enabled docker                 # enabled
# 以 deploy 身份验证免 sudo 用 docker (需 deploy 重新登录一次让 docker 组生效):
sudo -iu deploy docker run --rm hello-world
```

### 4/8 ufw
```bash
ufw status verbose
```
期望:`Default: deny (incoming), allow (outgoing)`,且 22/80/443 ALLOW。

### 5/8 fail2ban
```bash
systemctl is-active fail2ban                # active
fail2ban-client status                      # Jail list: sshd
fail2ban-client status sshd                 # 看到 maxretry / banned 信息
grep -R . /etc/fail2ban/jail.d/sshd.local   # 确认 maxretry=5 bantime=1h backend=systemd
```

### 6/8 自动安全更新
```bash
cat /etc/apt/apt.conf.d/20auto-upgrades             # 两个 "1"
cat /etc/apt/apt.conf.d/52unattended-upgrades-local # Automatic-Reboot "false" + 仅 security 源
systemctl is-enabled unattended-upgrades
unattended-upgrades --dry-run --debug 2>&1 | grep -iE 'allowed origins|Checking' | head
```

### 7/8 SSH 加固
```bash
sshd -T | grep -Ei '^(permitrootlogin|passwordauthentication|pubkeyauthentication)'
# 期望: permitrootlogin no / passwordauthentication no / pubkeyauthentication yes
```
从**本地**验证:
```bash
ssh deploy@149.28.149.67 'echo ok && sudo whoami'   # 应输出 ok 和 root
ssh root@149.28.149.67 echo nope                      # 应被拒绝 (Permission denied)
```

### 8/8 项目目录
```bash
ls -ld /opt/ko-tts                          # drwxr-xr-x ... deploy deploy
```

---

## 4. (验证通过后)给 deploy 加个 SSH 别名

本地可选,方便以后:`ssh kr-tts`

```bash
cat >> ~/.ssh/config <<'EOF'

Host kr-tts
    HostName 149.28.149.67
    User deploy
    IdentityFile ~/.ssh/id_ed25519
EOF
```

---

## 5. 万一被锁在外面 —— Vultr 网页 Console 救援

SSH 加固只影响 **SSH**;Vultr 的网页 Console 走的是本地 tty,**不受 `PermitRootLogin no` 影响**,
root 仍可用密码登录。

1. 登录 [Vultr 控制台](https://my.vultr.com/) → 选中该实例 → 右上角 **View Console**(noVNC)。
2. 用户名 `root`,密码用实例页面 **Overview → Password**(眼睛图标)里显示的 root 密码登录。
3. 解除 SSH 锁定,二选一:
   - **临时放开**(排查用):
     ```bash
     rm -f /etc/ssh/sshd_config.d/99-hardening.conf
     sshd -t && systemctl reload ssh
     ```
   - **只放开 root,保留禁密码**:编辑 `/etc/ssh/sshd_config.d/99-hardening.conf`,
     把 `PermitRootLogin no` 改成 `yes`,再 `sshd -t && systemctl reload ssh`。
4. 常见根因是 deploy 的 key 没装好,检查并修复:
   ```bash
   cat /home/deploy/.ssh/authorized_keys          # 是否有你的公钥
   chown -R deploy:deploy /home/deploy/.ssh
   chmod 700 /home/deploy/.ssh
   chmod 600 /home/deploy/.ssh/authorized_keys
   ```
5. 脚本每次运行都会把主配置备份成 `/etc/ssh/sshd_config.bak.<时间戳>`,
   已存在的 drop-in 备份成 `99-hardening.conf.bak.<时间戳>`,需要时可回滚。

> 找不到 root 密码?Vultr 实例页面可以 **Reset Root Password**(会重启实例)。
