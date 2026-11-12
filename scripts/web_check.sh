#!/usr/bin/env bash
# 网页实机校验：三档视口 + 截图 + 页面错误断言 —— 全部由 orca-cli 驱动。
#
# 用法：
#   bash scripts/web_check.sh [http://127.0.0.1:8000]
#   WEB_USER=alice WEB_PASS=secret bash scripts/web_check.sh   # 顺带验证登录
#
# 实测约束（2026-10-11，见计划 Task 7 Step 5，全是踩过的坑）：
#   * **每条命令都要带 `--page <browserPageId>`** —— 不带就打到"当前活动标签页"，
#     多开一个标签页就会录到/量到别的页面（实测被一个 example.com 标签页抢走过）；
#   * `orca screenshot` **不接受路径** —— 要写文件必须 `orca exec --command "screenshot <绝对路径>"`；
#   * 路径要给 Windows 风格（Git Bash 的 /d/... 它解析不了）：用 `cygpath -m`；
#   * React 受控输入用 `exec` 的 CSS `fill` **写不进去**（值会被 React 抹回去），
#     必须 `orca snapshot` 取 ref 再 `orca fill --element eN --value ...`；
#   * `orca eval` 的值在 `result.result` 里，而且是**字符串**（"true" 不是 true）；
#   * **视口要在 `goto` 之后设** —— 导航会把 emulation 重置回默认尺寸（实测 1077x909→截图 1615x1363，
#     三档截图一模一样大就是这么来的）；所以脚本里还断言了 `window.innerWidth` 真的等于目标宽度。
set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
OUT_DIR=".browser-check"
EXPECT_TITLE_SUBSTR="超星"
PAGE_ID=""

fail() {
    echo "[失败] $*" >&2
    exit 1
}

# 无论成败都关掉自己开的标签页：残留标签累积会让 orca 运行时退化（实测 goto 直接失败）
cleanup() {
    [ -n "${PAGE_ID:-}" ] && orca tab close --page "$PAGE_ID" --json >/dev/null 2>&1 || true
}
trap cleanup EXIT

command -v orca >/dev/null 2>&1 || fail "找不到 orca：先安装 Orca CLI 并确保它在 PATH 上"
curl -fsS -o /dev/null "$BASE/api/health" || fail "$BASE/api/health 不可达 —— 先起服务：uv run cx serve"

mkdir -p "$OUT_DIR"

# Git Bash 的 $(pwd) 是 /d/... 形式，Windows 侧的 orca 解析不了 —— 转成 D:/... 风格
if command -v cygpath >/dev/null 2>&1; then
    SHOT_BASE="$(cygpath -m "$(pwd)")"
else
    SHOT_BASE="$(pwd)"
fi

assert_no_page_errors() {
    local label="$1" errors
    errors="$(orca exec --command "errors" --page "$PAGE_ID" --json)" || fail "orca errors 执行失败"
    case "$errors" in
        *'"errors": []'*) ;;
        *) fail "$label 有页面 JS 错误：$errors" ;;
    esac
}

run_viewport() {
    local name="$1" width="$2" height="$3" low="$4" high="$5" shot fits title actual value
    echo "==> $name ${width}x${height}"

    orca goto --url "$BASE" --page "$PAGE_ID" --json >/dev/null
    orca wait --load networkidle --page "$PAGE_ID" --json >/dev/null
    # 视口必须在**导航之后**设：goto 会把 emulation 重置回默认尺寸（实测 1615x1363）
    orca exec --command "set viewport $width $height" --page "$PAGE_ID" --json >/dev/null

    # 断言用**区间**而不是精确值：orca 的 emulation 是有状态的（同一浏览器会话里设过 device
    # 之后，set viewport 会带上那台设备的缩放，实测目标 375 读到 411）。
    # 区间仍能抓住真正要防的故障：视口根本没生效（三档读数一模一样）。
    actual="$(orca eval --expression "String(window.innerWidth)" --page "$PAGE_ID" --json)"
    value="$(printf '%s' "$actual" | grep -oE '"result": "[0-9]+"' | grep -oE '[0-9]+')"
    [ -n "$value" ] || fail "$name 读不到视口宽度：$actual"
    if [ "$value" -lt "$low" ] || [ "$value" -gt "$high" ]; then
        fail "$name 视口没生效：读数 $value 不在 [$low, $high]（目标宽 $width）"
    fi

    title="$(orca get --what title --page "$PAGE_ID" --json)"
    case "$title" in
        *"$EXPECT_TITLE_SUBSTR"*) ;;
        *) fail "$name 页面标题不含「$EXPECT_TITLE_SUBSTR」（实际：$title）" ;;
    esac

    shot="$SHOT_BASE/$OUT_DIR/web-$name.png"
    orca exec --command "screenshot $shot" --page "$PAGE_ID" --json >/dev/null
    [ -s "$OUT_DIR/web-$name.png" ] || fail "$name 截图没落盘：$shot"

    fits="$(orca eval --expression \
        "document.documentElement.scrollWidth <= window.innerWidth + 1" --page "$PAGE_ID" --json)"
    case "$fits" in
        *'"result": "true"'*) ;;
        *) fail "$name 出现横向溢出：$fits" ;;
    esac

    assert_no_page_errors "$name"
    echo "    截图 $OUT_DIR/web-$name.png · 标题 ✓ · 无横向滚动 · 无页面错误"
}

PAGE_ID="$(orca tab create --json | grep -o '"browserPageId": "[^"]*"' | head -1 | sed 's/.*: "//;s/"$//')"
[ -n "$PAGE_ID" ] || fail "orca tab create 没返回 browserPageId"
echo "标签页：$PAGE_ID"

for spec in "mobile 375 812 300 500" "tablet 768 1024 501 950" "desktop 1280 800 951 4000"; do
    # shellcheck disable=SC2086
    set -- $spec
    run_viewport "$1" "$2" "$3" "$4" "$5"
done

if [ -n "${WEB_USER:-}" ] && [ -n "${WEB_PASS:-}" ]; then
    echo "==> 登录（WEB_USER 已提供）"
    if [ -n "${WEB_CLEAR_COOKIES:-}" ]; then
        # 显式清 cookie：否则上一次跑留下的会话会让 /login 直接跳到仪表盘，登录路径就验不到了
        orca exec --command "cookies clear" --page "$PAGE_ID" --json >/dev/null
    fi
    orca goto --url "$BASE/login" --page "$PAGE_ID" --json >/dev/null
    orca wait --load networkidle --page "$PAGE_ID" --json >/dev/null

    where="$(orca get --what url --page "$PAGE_ID" --json)"
    if [[ "$where" != *"/login"* ]]; then
        echo "    [跳过] 浏览器已有会话（当前 $where）；要验登录流程请加 WEB_CLEAR_COOKIES=1"
    else
        snap="$(orca snapshot --page "$PAGE_ID" 2>/dev/null || true)"
        # 快照里的 ref 可能带复合属性（`[disabled, ref=e4]`），所以先按角色过滤再取 ref
        user_ref="$(printf '%s\n' "$snap" | grep -oE '\- textbox .*ref=e[0-9]+' | sed -n 1p | grep -oE 'e[0-9]+$')"
        pass_ref="$(printf '%s\n' "$snap" | grep -oE '\- textbox .*ref=e[0-9]+' | sed -n 2p | grep -oE 'e[0-9]+$')"
        login_ref="$(printf '%s\n' "$snap" | grep -oE '\- button .*ref=e[0-9]+' | sed -n 1p | grep -oE 'e[0-9]+$')"
        [ -n "$user_ref" ] && [ -n "$pass_ref" ] && [ -n "$login_ref" ] \
            || fail "登录页快照里没找到输入框 / 登录按钮的 ref"

        orca fill --element "$user_ref" --value "$WEB_USER" --page "$PAGE_ID" --json >/dev/null
        orca fill --element "$pass_ref" --value "$WEB_PASS" --page "$PAGE_ID" --json >/dev/null
        # 登录按钮在两个输入框都生效前是 disabled 的：直接点会点空（实测竞态）
        for _ in 1 2 3 4 5 6 7 8; do
            line="$(orca snapshot --page "$PAGE_ID" 2>/dev/null | grep -oE '\- button .*ref=e[0-9]+' | sed -n 1p || true)"
            case "$line" in
                *disabled*) sleep 1 ;;
                *) login_ref="$(printf '%s' "$line" | grep -oE 'e[0-9]+$')" && break ;;
            esac
        done
        orca click --element "$login_ref" --page "$PAGE_ID" --json >/dev/null
        orca wait --load networkidle --page "$PAGE_ID" --json >/dev/null

        # 登录是 XHR + 客户端跳转：networkidle 可能在跳转之前就返回了，所以有界等待
        for _ in 1 2 3 4 5 6 7 8 9 10; do
            where="$(orca get --what url --page "$PAGE_ID" --json)"
            [[ "$where" == *"/login"* ]] || break
            sleep 1
        done
        case "$where" in
            *"/login"*) fail "登录后仍停在登录页 —— 检查 WEB_USER/WEB_PASS（首个用户需先在网页注册）" ;;
        esac

        orca exec --command "screenshot $SHOT_BASE/$OUT_DIR/web-logged-in.png" \
            --page "$PAGE_ID" --json >/dev/null
        assert_no_page_errors "登录后"
        echo "    登录成功 · 截图 $OUT_DIR/web-logged-in.png"
    fi
else
    echo "[提示] 未提供 WEB_USER/WEB_PASS —— 只校验未登录形态（登录页）"
fi

# 脚本自己开的标签页自己收掉（截图已经落盘，页面对排查没有额外价值）
orca tab close --page "$PAGE_ID" --json >/dev/null 2>&1 || true

echo "[通过] 网页检查完成（截图在 $OUT_DIR/）"
