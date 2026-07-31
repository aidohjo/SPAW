#!/usr/bin/env bash
#
# Assistant 项目启动脚本
# ========================
# 自动扫描 src/*.py 文件头部的 import 语句，
# 识别第三方依赖（排除标准库和本地模块），
# 自动安装缺失的包，然后启动。
#
# 用法: ./start.sh              — 默认启动 TUI（Xcode 风格界面）
#       ./start.sh --repl       — 启动命令行 REPL 模式
#       ./start.sh --debug      — TUI + 调试模式
#       ./start.sh -d           — 同上
#

set -euo pipefail

# ===================== 色彩定义 =====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC} $*"; }
log_ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
log_found() { echo -e "  ${CYAN}📦${NC} 发现外部依赖: ${BOLD}$*${NC}"; }

# ===================== 参数解析 =====================
MODE="tui"
PASSTHROUGH_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repl|--cli)
            MODE="repl"
            shift
            ;;
        *)
            PASSTHROUGH_ARGS+=("$1")
            shift
            ;;
    esac
done

# ===================== 路径推导 =====================
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${PROJECT_ROOT}/src"
VENV_DIR="${PROJECT_ROOT}/.env"
CORE_PY="${SRC_DIR}/core.py"
TUI_PY="${SRC_DIR}/tui.py"

# ===================== 横幅 =====================
echo ""
if [ "$MODE" = "tui" ]; then
    echo -e "${MAGENTA}╔══════════════════════════════════════╗${NC}"
    echo -e "${MAGENTA}║     Assistant · SPAW TUI  v2.0      ║${NC}"
    echo -e "${MAGENTA}╚══════════════════════════════════════╝${NC}"
else
    echo -e "${CYAN}╔══════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║       Assistant · REPL 模式          ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════╝${NC}"
fi
echo ""

# ===================== 步骤 0: 检查 API Key =====================
log_step "0/5  检查 DEEPSEEK_API_KEY..."

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
    log_error "环境变量 DEEPSEEK_API_KEY 未设置"
    echo "  请先执行: export DEEPSEEK_API_KEY=你的密钥" >&2
    exit 1
fi
log_ok "DEEPSEEK_API_KEY 已设置"

# ===================== 步骤 1: 检查项目文件 =====================
log_step "1/5  检查项目文件..."

for f in "$CORE_PY" "$TUI_PY"; do
    if [ ! -f "$f" ]; then
        log_error "找不到 $(basename "$f")，期望路径: $f"
        if [ "$f" = "$TUI_PY" ] && [ "$MODE" = "tui" ]; then
            log_info "提示：使用 ./start.sh --repl 启动命令行模式"
        fi
        exit 1
    fi
done

log_ok "core.py"
log_ok "tui.py"

# ===================== 步骤 2: 检查/创建虚拟环境 =====================
log_step "2/5  检查虚拟环境..."

if [ ! -d "$VENV_DIR" ]; then
    log_warn "虚拟环境不存在，正在创建: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    log_ok "虚拟环境创建完成"
else
    log_ok "虚拟环境已存在: $VENV_DIR"
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
log_ok "虚拟环境已激活 ($(python3 --version 2>&1))"

# ===================== 步骤 3: 检测 pip 环境 =====================
log_step "3/5  检测 pip 环境..."

# --- 3a. 检查 pip 是否可用 ---
if ! command -v pip &>/dev/null; then
    log_error "pip 不可用，请检查虚拟环境是否正常"
    exit 1
fi

PIP_VERSION=$(pip --version 2>&1)
log_ok "pip 可用 — ${PIP_VERSION}"

# --- 3b. 显示 pip 源配置 ---
PIP_INDEX=$(python3 -c "
import sys
# 优先检测 pip.conf 配置的 index-url
try:
    import pip._internal.configuration as config
    cfg = config.Configuration()
    cfg.load()
    val = cfg.get_value('global.index-url') or cfg.get_value('install.index-url')
    if val:
        print(val)
        sys.exit(0)
except Exception:
    pass

# 回退：用 pip config get 命令
import subprocess
try:
    r = subprocess.run([sys.executable, '-m', 'pip', 'config', 'get', 'global.index-url'],
                       capture_output=True, text=True, timeout=3)
    if r.returncode == 0 and r.stdout.strip():
        print(r.stdout.strip())
except Exception:
    pass
" 2>/dev/null || true)

if [ -n "$PIP_INDEX" ]; then
    log_ok "pip 源: ${PIP_INDEX}"
else
    log_ok "pip 源: 默认 (PyPI)"
fi

# --- 3c. 检查 pip 是否有代理/网络配置问题 ---
if pip config get global.proxy &>/dev/null 2>&1; then
    PROXY_VAL=$(pip config get global.proxy 2>/dev/null || true)
    if [ -n "$PROXY_VAL" ]; then
        log_ok "pip 代理: ${PROXY_VAL}"
    fi
fi

# --- 3d. 对已安装包做完整性检查 (pip check) ---
echo ""
log_info "正在验证已安装包完整性..."
if pip check &>/dev/null; then
    log_ok "依赖完整性检查通过（无冲突/破损）"
else
    echo ""
    log_warn "依赖完整性检查发现问题:"
    pip check 2>&1 | while IFS= read -r line; do
        echo -e "  ${YELLOW}⚠${NC}  $line"
    done
    echo ""
    log_info "上述问题不影响启动，但建议在虚拟环境中运行:"
    echo -e "  ${CYAN}pip install --upgrade <冲突包名>${NC}"
    echo ""
fi

echo ""

# ===================== 步骤 4: 自动扫描源码提取依赖 =====================
log_step "4/5  扫描源码提取 Python 依赖..."

# ------------------------------------------------------------
# 核心逻辑：扫描 src/*.py 文件头部的 import 语句，
# 自动识别第三方依赖（排除标准库 + 本地模块）。
# 无需手动维护 requirements.txt。
# ------------------------------------------------------------
EXTERNAL_PKGS=$(python3 -c "
import re, sys
from pathlib import Path

src = Path('${SRC_DIR}')
stdlib = sys.stdlib_module_names                          # Python ≥3.10
local_mods = {f.stem for f in src.glob('*.py')}           # 本地 .py 文件名

pkgs = set()
for pyfile in sorted(src.glob('*.py')):
    try:
        with open(pyfile, encoding='utf-8') as f:
            for line in f:
                # 匹配: import xxx  或  from xxx import yyy
                m = re.match(r'^(?:import\s+|from\s+)([a-zA-Z_][a-zA-Z0-9_]*)', line)
                if m:
                    name = m.group(1)
                    if name not in stdlib and name not in local_mods:
                        pkgs.add(name)
    except Exception:
        pass

for p in sorted(pkgs):
    print(p)
")

if [ -z "$EXTERNAL_PKGS" ]; then
    log_ok "未发现外部依赖（纯标准库项目）"
else
    echo ""
    MISSING=()
    for pkg in $EXTERNAL_PKGS; do
        if pip show "$pkg" &>/dev/null; then
            ver=$(pip show "$pkg" 2>/dev/null | grep -E '^Version:' | awk '{print $2}')
            echo -e "  ${GREEN}✓${NC} ${BOLD}${pkg}${NC} ${ver}"
        else
            echo -e "  ${RED}✗${NC} ${BOLD}${pkg}${NC}  — 未安装"
            MISSING+=("$pkg")
        fi
    done
    echo ""

    if [ ${#MISSING[@]} -gt 0 ]; then
        log_warn "缺少 ${#MISSING[@]} 个依赖: ${MISSING[*]}"
        log_info "正在安装..."
        pip install --upgrade pip --quiet
        pip install "${MISSING[@]}" --quiet
        log_ok "依赖安装完成"
    else
        log_ok "所有依赖已满足"
    fi
fi

echo ""

# ===================== 步骤 5: 启动 =====================
if [ "$MODE" = "tui" ]; then
    log_step "5/5  启动 TUI 界面 ..."
    echo ""
    echo -e "  ${MAGENTA}提示:${NC} Ctrl+Q 退出 | Ctrl+D 切换调试面板 | Ctrl+L 清屏 | Esc 聚焦输入"
    echo -e "  ${MAGENTA}回退:${NC} ./start.sh --repl  使用命令行模式"
    echo ""

    cd "$SRC_DIR"
    exec python3 "$TUI_PY" "${PASSTHROUGH_ARGS[@]}"
else
    log_step "5/5  启动 REPL 命令行模式 ..."
    echo ""

    cd "$SRC_DIR"
    exec python3 "$CORE_PY" "${PASSTHROUGH_ARGS[@]}"
fi
