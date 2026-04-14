# OpsAgent LoongArch 适配分析报告

> **开发环境**：Ubuntu 22.04/24.04 (x86_64)  
> **目标部署环境**：银河麒麟高级服务器操作系统 V10/V11 (LoongArch64)  
> **结论先行**：Python 纯自研方案在 LoongArch 上的适配难度远低于 C/C++ 项目，但有几个关键坑需要提前规避

---

## 一、指令集架构对比

### 1.1 二进制兼容性

| 维度 | x86_64 | LoongArch64 |
|------|--------|-------------|
| 指令集 | CISC，历史包袱重 | RISC，参考 MIPS/RISC-V 设计，2021 年发布 |
| 字节序 | 小端 | 小端（与 x86 一致，减少移植问题）|
| 寄存器 | 16 个通用寄存器 | 32 个通用寄存器 |
| 二进制兼容 | ❌ 与 LoongArch 完全不兼容 | 无法直接运行 x86_64 ELF |
| ABI | System V AMD64 ABI | LoongArch LP64D ABI |

**对本项目的影响**：Python 解释器本身是 C 编译的二进制，需要 LoongArch 原生版本。但 Python 源码（`.py` 文件）完全可移植，**我们的业务代码零改动**。

### 1.2 编译工具链

| 工具 | x86_64 状态 | LoongArch64 状态 |
|------|------------|-----------------|
| GCC | 完整支持 | GCC 12+ 原生支持，Kylin 仓库提供 |
| LLVM/Clang | 完整支持 | LLVM 16+ 支持，但不如 GCC 成熟 |
| 交叉编译 | `gcc-loongarch64-linux-gnu` | 需手动安装 |
| Python CPython | 3.12+ 官方支持 | 3.12+ 官方支持（2023 年合并主线）|

### 1.3 Glibc 版本差异

| 环境 | Glibc 版本 | 影响 |
|------|-----------|------|
| Ubuntu 22.04 (x86_64) | 2.35 | 开发环境 |
| Ubuntu 24.04 (x86_64) | 2.39 | 开发环境 |
| 银河麒麟 V10 (LoongArch) | ~2.17（CentOS 7 基线）| ⚠️ 较旧 |
| 银河麒麟 V11 (LoongArch) | ~2.28-2.35 | Linux 6.6 内核 |

**关键风险**：Kylin V10 的 glibc 2.17 非常旧，部分 Python 包的 C 扩展可能依赖更新的 glibc 符号。

---

## 二、本项目在 LoongArch 的支持程度

### 2.1 Python 运行时

| 组件 | 支持状态 | 说明 |
|------|---------|------|
| Python 3.12+ | ✅ 官方支持 | 2023 年合并 CPython 主线 |
| Python 3.10/3.11 | ⚠️ 需补丁 | 非官方，Kylin 仓库可能提供 |
| asyncio | ✅ 标准库，完全支持 | |
| sqlite3 | ✅ 标准库，完全支持 | |
| subprocess | ✅ 标准库，完全支持 | |
| os.setuid / os.setgid | ✅ POSIX 标准，完全支持 | 权限隔离核心功能 |
| resource.setrlimit | ✅ POSIX 标准，完全支持 | |

### 2.2 关键依赖包

| 包 | PyPI Wheel | LoongArch 状态 | 解决方案 |
|----|-----------|---------------|---------|
| `openai` | 纯 Python | ✅ 直接安装 | 无需处理 |
| `httpx` | 纯 Python | ✅ 直接安装 | 无需处理 |
| `python-dotenv` | 纯 Python | ✅ 直接安装 | 无需处理 |
| `pyyaml` | 有 C 扩展 | ⚠️ 需源码编译 | `pip install --no-binary pyyaml pyyaml` |
| `pydantic v2` | 有 Rust 扩展 | ⚠️ 需源码编译 | 见下方说明 |
| `rich` | 纯 Python | ✅ 直接安装 | 无需处理 |
| `anyio` | 纯 Python | ✅ 直接安装 | 无需处理 |

**pydantic v2 的特殊情况**：pydantic v2 核心用 Rust 编写（pydantic-core），LoongArch 无预编译 wheel。

```bash
# 解决方案 A：降级到 pydantic v1（纯 Python）
pip install "pydantic<2"

# 解决方案 B：从源码编译（需要 Rust 工具链）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
pip install pydantic  # 会自动从源码编译

# 解决方案 C：完全不用 pydantic（推荐）
# 我们的项目用 dataclass 替代，零外部依赖
```

**推荐**：本项目用 `dataclass` 替代 pydantic，彻底规避这个问题。

### 2.3 本项目特有功能的 LoongArch 兼容性

| 功能 | 实现方式 | LoongArch 兼容性 |
|------|---------|----------------|
| setuid 降权执行 | `os.setuid()` POSIX 调用 | ✅ 完全兼容 |
| resource 限制 | `resource.setrlimit()` | ✅ 完全兼容 |
| subprocess 执行 | Python 标准库 | ✅ 完全兼容 |
| SQLite 持久化 | Python 标准库 sqlite3 | ✅ 完全兼容 |
| JSONL 审计日志 | 纯文件 I/O | ✅ 完全兼容 |
| Hook 脚本执行 | subprocess + shell | ✅ 完全兼容 |
| lsof/netstat/journalctl | 系统命令调用 | ✅ Kylin 均有提供 |

**结论：本项目核心功能在 LoongArch 上兼容性极好**，因为我们的安全层、权限层、审计层全部基于 POSIX 标准接口，没有 x86 特定代码。

---

## 三、开发策略建议

### 3.1 分层验证策略

不需要一开始就搞交叉编译，按以下顺序验证：

```
阶段 1（开发期，x86_64 Ubuntu）
  → 正常开发，所有功能在 x86 上跑通
  → 使用 QEMU 用户态模拟做快速兼容性检查

阶段 2（集成期，QEMU 全系统模拟）
  → 在 x86 上运行 LoongArch64 虚拟机
  → 验证 Python 包安装、setuid 行为、系统调用

阶段 3（验收期，真实硬件或 Kylin 虚拟机）
  → 在真实 Kylin V10/V11 LoongArch 环境验证
  → 重点测试：包安装、权限隔离、系统工具调用
```

### 3.2 QEMU 用户态模拟（最快速的验证方式）

```bash
# Ubuntu 22.04/24.04 安装
sudo apt install qemu-user-static binfmt-support

# 注册 LoongArch64 binfmt
sudo update-binfmts --enable

# 验证：直接运行 LoongArch64 ELF 二进制
# （需要先有 LoongArch64 的 Python 二进制）
```

**更实用的方式：Docker 多架构容器**

```bash
# 1. 安装 QEMU 和 buildx
sudo apt install qemu-user-static
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes

# 2. 拉取 LoongArch64 的 Linux 镜像（社区维护）
# 注意：官方 Docker Hub 暂无 loongarch64 镜像
# 使用龙芯社区镜像
docker pull --platform linux/loong64 cr.loongnix.cn/library/debian:sid

# 3. 在容器内测试 Python 包安装
docker run --rm --platform linux/loong64 \
  cr.loongnix.cn/library/python:3.11 \
  pip install openai httpx pyyaml rich
```

### 3.3 交叉编译（仅在需要编译 C 扩展时使用）

```bash
# 安装交叉编译工具链
sudo apt install gcc-loongarch64-linux-gnu \
                 g++-loongarch64-linux-gnu \
                 binutils-loongarch64-linux-gnu

# 验证工具链
loongarch64-linux-gnu-gcc --version

# 交叉编译 Python C 扩展示例（如 pyyaml）
pip install --no-binary pyyaml \
    --build-option "--compiler=loongarch64-linux-gnu-gcc" \
    pyyaml
```

**实际上本项目几乎不需要交叉编译**，因为我们的依赖大部分是纯 Python。

### 3.4 requirements.txt 的 LoongArch 适配版本

```txt
# requirements.txt（LoongArch 兼容版）

# 纯 Python 包，直接安装，无问题
openai>=1.30.0
httpx>=0.27.0
python-dotenv>=1.0.0
pyyaml>=6.0          # 有 C 扩展，但可降级到纯 Python 版
rich>=13.0.0
anyio>=4.0.0

# 避免使用 pydantic v2（Rust 扩展，LoongArch 无 wheel）
# 改用 dataclass（标准库，零依赖）

# 测试
pytest>=8.0.0
pytest-asyncio>=0.23.0
pytest-mock>=3.12.0
```

---

## 四、银河麒麟 V10 vs V11 关键差异

| 维度 | Kylin V10 | Kylin V11 |
|------|-----------|-----------|
| 内核 | 4.19.x（老旧）| 6.6（现代）|
| glibc | ~2.17 | ~2.28-2.35 |
| Python 默认 | 3.6/3.8 | 3.8/3.10+ |
| 包管理 | yum/dnf (RPM) | yum/dnf (RPM) |
| SELinux | 可能启用 | 可能启用 |
| systemd | 有 | 有 |
| journalctl | ✅ | ✅ |

**重要提醒**：

```bash
# Kylin V10 内核 4.19 的潜在问题
# io_uring 不支持（4.19 没有）→ 但我们不用 io_uring，无影响
# 某些 cgroup v2 特性不支持 → 我们不用容器，无影响

# Kylin 上安装 Python 3.12
sudo dnf install python3.12 python3.12-pip  # 如果仓库有
# 或从源码编译
wget https://www.python.org/ftp/python/3.12.0/Python-3.12.0.tgz
./configure --prefix=/usr/local && make && sudo make install
```

---

## 五、SELinux 对 setuid 的影响

这是本项目最需要关注的部署风险：

```bash
# 检查 SELinux 状态
getenforce   # Enforcing / Permissive / Disabled

# 如果是 Enforcing，setuid 可能被阻止
# 解决方案 A：临时设为 Permissive（演示环境可接受）
sudo setenforce 0

# 解决方案 B：为 ops-agent 添加 SELinux 策略（生产推荐）
# 创建自定义策略模块
cat > ops_agent.te << 'EOF'
module ops_agent 1.0;
require {
    type unconfined_t;
    class process setuid;
}
allow unconfined_t self:process setuid;
EOF
checkmodule -M -m -o ops_agent.mod ops_agent.te
semodule_package -o ops_agent.pp -m ops_agent.mod
sudo semodule -i ops_agent.pp

# 解决方案 C：用 sudo 白名单替代 setuid（最安全）
# /etc/sudoers.d/ops-agent
ops-agent ALL=(ops-writer) NOPASSWD: /usr/bin/rm, /usr/bin/chmod
```

**建议**：在 `main.py` 启动时检测 SELinux 状态，如果是 Enforcing 则打印警告。

---

## 六、综合适配建议

### 优先级 P0（必须处理，否则无法运行）

1. **确认 Python 版本**：目标环境必须有 Python 3.10+，建议 3.12
   ```bash
   # 在 Kylin 上验证
   python3 --version
   pip3 install openai httpx pyyaml rich
   ```

2. **不使用 pydantic v2**：改用 `dataclass`，已在设计中体现

3. **SELinux 检测**：启动时检查并给出明确提示

### 优先级 P1（应该处理，影响稳定性）

4. **系统工具路径**：Kylin 上部分工具路径可能不同
   ```python
   # 不要硬编码路径，用 shutil.which() 查找
   import shutil
   LSOF = shutil.which("lsof") or "/usr/sbin/lsof"
   NETSTAT = shutil.which("netstat") or "/usr/bin/netstat"
   ```

5. **journalctl 可用性**：Kylin V10/V11 均有 systemd，journalctl 可用

6. **glibc 版本兼容**：避免使用 glibc 2.17 之后才有的 C 扩展

### 优先级 P2（建议处理，提升体验）

7. **QEMU 验证环境搭建**：Week 4 之前搭好，用于提前验证 setuid 行为

8. **Docker 多架构测试**：CI 中加入 LoongArch64 容器测试

9. **RPM 打包**：竞赛提交时提供 `.rpm` 包，符合 Kylin 的包管理习惯

---

## 七、一句话总结

> **本项目是 Python 纯自研方案，核心依赖全是 POSIX 标准接口（setuid/subprocess/sqlite3），LoongArch 适配难度极低。唯一需要提前处理的是：不用 pydantic v2、检测 SELinux 状态、确认目标环境 Python 版本 ≥ 3.10。**

与 C/C++ 项目相比，我们的跨架构适配工作量约为其 1/10。
