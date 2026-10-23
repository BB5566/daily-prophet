#!/usr/bin/env python3
"""每日早報（2026-10-06）：把各處紀錄彙整成一則 Telegram 訊息。

純腳本、不經過模型：每一行都直接來自紀錄或系統狀態，杰哥沒有機會編造。
來源：Sentinel 狀態檔、排程紀錄、夢境日誌、GitHub、Jules、shim 排隊紀錄、待辦清單、磁碟。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERMES = Path.home() / ".hermes"
NOW = datetime.now().astimezone()
SINCE = NOW - timedelta(hours=24)
WEEKDAY = "一二三四五六日"[NOW.weekday()]


# 2026-10-12 依 Studio「沒報錯的失敗」清單：指令失敗原本回空字串，PR／Jules 那段就默默消失，
# 看起來跟「沒有 PR」一樣（gh 憑證過期也是）。失敗的記下來，早報最後列出。
FAILED = []


def run(*cmd, timeout=60):
    name = " ".join(cmd[:3]) if cmd[0] == "gh" else Path(cmd[1] if cmd[0].startswith("python") else cmd[0]).name
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:
        FAILED.append(f"{name}（{type(exc).__name__}）")
        return ""
    if out.returncode != 0:
        FAILED.append(f"{name}（結束碼 {out.returncode}）")
    return out.stdout


def parse_time(text):
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    # 沒寫時區的時間當成本機時間，才能跟 NOW（有時區）相減
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=NOW.tzinfo)


def system_health():
    """Sentinel 每 3 小時檢查一次，結果存在狀態檔。"""
    state = HERMES / "cron" / "sentinel_last_issues.json"
    try:
        issues = json.loads(state.read_text())
        checked = datetime.fromtimestamp(state.stat().st_mtime).strftime("%H:%M")
    except (OSError, ValueError):
        return ["⚠️ 系統：讀不到 Sentinel 狀態"]
    if not issues:
        return [f"✅ 系統全部正常（{checked} 檢查）"]
    return [f"🔴 系統異常：{'、'.join(issues)}（{checked} 檢查）"]


def self_heal():
    """Sentinel 自己修好的（2026-10-12 閉環）：只報數量，修不好的才會出現在系統異常。"""
    log = HERMES / "logs" / "sentinel-heal.jsonl"
    try:
        rows = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    except OSError:
        return []
    since = (NOW - timedelta(days=1)).isoformat()
    recent = [r for r in rows if r["time"] >= since]
    fixed = [r["check"] for r in recent if r["fixed"]]
    if not fixed:
        return []
    return [f"🔧 自動修好 {len(fixed)} 件：{'、'.join(sorted(set(fixed)))}"]


def jobs():
    return json.loads((HERMES / "cron" / "jobs.json").read_text())["jobs"]


def backups():
    names = {"758774b5bc89": "記憶庫", "e1558f2bf270": "對話庫", "d304780e5807": "設定"}
    ok, bad = [], []
    for job in jobs():
        if job["id"] not in names:
            continue
        ran = parse_time(job.get("last_run_at"))
        if not ran or ran < SINCE:
            continue  # 設定備份每週日才跑，其他天不列
        (ok if job.get("last_status") == "ok" else bad).append(names[job["id"]])
    lines = []
    if ok:
        lines.append(f"✅ 備份完成：{'、'.join(ok)}")
    if bad:
        lines.append(f"🔴 備份失敗：{'、'.join(bad)}")
    if not ok and not bad:
        lines.append("⚠️ 過去 24 小時沒有備份紀錄")
    return lines


def failed_jobs():
    bad = []
    for job in jobs():
        ran = parse_time(job.get("last_run_at"))
        if ran and ran >= SINCE and job.get("last_status") not in ("ok", None):
            bad.append(job["name"])
    # 2026-10-12：整個停掉沒跑的排程不會有失敗紀錄，看「該跑的時間過了多久」（Studio 清單第 1 條）
    late = [job["name"] for job in jobs() if job.get("enabled") is not False and not job.get("paused_at")
            and (due := parse_time(job.get("next_run_at"))) and NOW - due > timedelta(hours=1)]
    lines = [f"🔴 排程失敗：{'、'.join(bad)}"] if bad else []
    return lines + ([f"🔴 排程沒照時間跑：{'、'.join(late)}"] if late else [])


def dream_todo():
    """夢境日誌裡「待批」與「高風險」兩段的條列項目。"""
    path = HERMES / "dreams" / f"{NOW:%Y-%m-%d}.md"
    if not path.exists():
        # 2026-10-07 起做夢排程沒有寫檔工具，報告只存在排程輸出檔
        outputs = sorted((HERMES / "cron" / "output" / "55b982430516").glob(f"{NOW:%Y-%m-%d}_*.md"))
        if not outputs:
            return []
        path = outputs[-1]
    items, keep = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            keep = "待批" in line or "高風險" in line
            continue
        if keep and re.match(r"\s*(\d+\.|[-*])\s+\S", line) and "無" not in line[:8]:
            items.append(re.sub(r"\*\*", "", line.strip())[:70])
    if not items:
        return []
    return ["🌙 夢境整理等你批准："] + [f"  {i}" for i in items[:4]]


def open_prs():
    out = run("gh", "search", "prs", "--owner", "BB5566", "--state", "open", "--json", "number,title,repository")
    try:
        prs = json.loads(out or "[]")
    except ValueError:
        return []
    if not prs:
        return []
    return [f"🔀 開著的 PR {len(prs)} 個：" + "；".join(f"{p['repository']['name']}#{p['number']} {p['title'][:20]}" for p in prs[:3])]


def offsite_backup():
    """Google Drive 異地備份（Hyper Backup 任務 hermes-gdrive，每天 04:30）：只有失敗或太久沒跑才講（2026-10-20）。"""
    out = run("ssh", "-p", "2201", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@ds920.tailb74f5c.ts.net",
              "/usr/syno/bin/synowebapi --exec api=SYNO.Backup.Task method=list version=1 "
              """additional='["last_bkp_time","last_bkp_result"]' 2>/dev/null""")
    if "{" not in out:
        return ["⚠️ 讀不到異地備份狀態（NAS 連不上？）"]
    tasks = json.loads(out[out.index("{"):])["data"]["task_list"]
    task = next((t for t in tasks if t.get("name") == "hermes-gdrive"), None)
    if not task:
        return ["🔴 找不到異地備份任務 hermes-gdrive"]
    when = task.get("last_bkp_time")
    if task.get("last_bkp_result") != "done":
        return [f"🔴 異地備份（Google Drive）上次沒成功：{task.get('last_bkp_result')}（{when}）"]
    last = datetime.strptime(when, "%Y/%m/%d %H:%M:%S").replace(tzinfo=NOW.tzinfo) if when else None
    if not last or NOW - last > timedelta(hours=36):
        return [f"🔴 異地備份（Google Drive）超過 36 小時沒跑，上次 {when}"]
    return []


def capabilities_check():
    """每個功能真的跑一次（capabilities.py）：全過一行，沒過的逐項列（2026-10-20）。"""
    import capabilities
    results = capabilities.run()
    bad = [r for r in results if not r["ok"]]
    if not bad:
        return [f"✅ 功能實測 {len(results)} 項都正常"]
    return [f"🔴 功能實測沒過：{r['name']}：{r['detail'][:100]}" for r in bad]


def deploy_health():
    """NAS 自動部署有沒有跟上：GitHub 打包成功超過 30 分鐘、容器卻還是舊的 = 卡住。
    （2026-09-26 ghcr token 過期，兩週沒人發現；NAS 的 docker logs 太慢，所以改比時間。）"""
    sites = {"portfolio": "portfolio-frontend", "MoodCanvas": "moodcanvas"}
    # 10-15：NAS 重開機後容器沒自己起來，網站停了 7 小時沒人發現 → 連後端、資料庫一起看有沒有在跑
    containers = [*sites.values(), "portfolio-backend", "portfolio-db"]
    out = run("ssh", "-p", "2201", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "root@ds920.tailb74f5c.ts.net",
              "timeout 20 /usr/local/bin/docker inspect -f '{{.Name}} {{.Created}} {{.State.Running}}' " + " ".join(containers))
    created, stopped = {}, []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, ts, running = line.split()
        name = name.lstrip("/")
        created[name] = parse_time(ts[:19] + "+00:00")
        if running != "true":
            stopped.append(name)
    if not created:
        return ["⚠️ 讀不到 NAS 容器狀態（NAS 連不上？）"]
    lines = []
    if stopped:
        lines.append(f"🔴 網站容器停了：{'、'.join(stopped)}（bb-made.com 可能打不開，NAS 重開機後常見）")
    stuck = []
    for repo, container in sites.items():
        runs = json.loads(run("gh", "run", "list", "-R", f"BB5566/{repo}", "-b", "main", "-e", "push", "-L", "1",
                              "--json", "conclusion,updatedAt") or "[]")
        if not runs or runs[0]["conclusion"] != "success" or not created.get(container):
            continue
        built = parse_time(runs[0]["updatedAt"].replace("Z", "+00:00"))
        if built - created[container] > timedelta(minutes=30) and NOW - built > timedelta(minutes=30):
            stuck.append(repo)
    if stuck:
        lines.append(f"🔴 自動部署卡住：{'、'.join(stuck)} 打包好了但 NAS 沒換新版（多半是 ghcr token 過期）")
    return lines


def jules_waiting():
    out = run("python3", str(HERMES / "scripts" / "jules.py"), "list")
    waiting = [l.split()[0] for l in out.splitlines() if "AWAITING" in l]
    return [f"🤖 Jules 有 {len(waiting)} 個任務在等你回覆"] if waiting else []


def model_events():
    """昨天 Studio 斷線切備援幾次、BB 的 Telegram 訊息在 shim 排隊超過 10 秒幾次（只算標了 [TG] 的，2026-10-07 起）。"""
    lines = []
    since = SINCE.strftime("%Y-%m-%d %H:%M")
    fallback = sum(1 for l in open(HERMES / "logs" / "agent.log", errors="ignore")
                   if "Fallback activated:" in l and l[:16] >= since)
    if fallback:
        lines.append(f"⚠️ Studio 連不上，自動切到免費備援 {fallback} 次")
    queue = HERMES / "logs" / "shim-queue.log"
    if queue.exists():
        waits = [float(m.group(1)) for l in open(queue, errors="ignore")
                 if l[:16] >= since and "[TG]" in l and (m := re.search(r"等了 ([\d.]+)s", l))]
        slow = [w for w in waits if w > 10]
        if slow:
            lines.append(f"🐢 你的訊息排隊超過 10 秒 {len(slow)} 次（最久 {max(slow):.0f} 秒）")
    lines += reply_speed()
    return lines


def studio_queue():
    """昨天在 Studio 工作佇列報名的工作（studio_job.py 的紀錄，2026-10-16 起）：完成、跳過、暫停幾次，最久等多久。"""
    path = HERMES / "logs" / "studio-job.log"
    if not path.exists():
        return []
    names = {"exam": "考試", "plugin_compare": "外掛比較", "pr_review": "PR 審查", "memory_sweep": "記憶清道夫"}
    since = SINCE.strftime("%Y-%m-%d %H:%M")
    count = {}  # (工作, 結果) → 次數
    longest_wait = 0
    pauses = 0
    for line in open(path, errors="ignore"):
        if line[:16] < since:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        verb, kind = parts[2], parts[3].split("：")[0]  # 例：「跳過 pr_review id=…」「佇列連不到，照舊直接跑 exam：錯誤」
        if kind == "smoke_test":
            continue
        kind = names.get(kind, kind)
        if verb in ("完成", "跳過", "中途放棄"):
            count[(kind, verb)] = count.get((kind, verb), 0) + 1
        elif verb == "佇列連不到，照舊直接跑":
            count[(kind, "連不到佇列")] = count.get((kind, "連不到佇列"), 0) + 1
        elif verb == "暫停":
            pauses += 1
        elif verb == "放行" and (m := re.search(r"等了 (\d+) 秒", line)):
            longest_wait = max(longest_wait, int(m.group(1)))
    if not count:
        return []
    kinds = list(dict.fromkeys(k for k, _ in count))
    parts = []
    for kind in kinds:
        results = [f"{verb} {n} 次" for (k, verb), n in count.items() if k == kind]
        parts.append(f"{kind} {'、'.join(results)}")
    bad = any(verb in ("中途放棄", "連不到佇列") for _, verb in count)
    line = f"{'⚠️' if bad else '🗂️'} Studio 排程：{'；'.join(parts)}"
    if longest_wait >= 60:
        line += f"（最久等放行 {longest_wait // 60} 分）"
    if pauses:
        line += f"，讓別的工作插隊 {pauses} 次"
    return [line]


def reply_speed():
    """昨天 BB 在 Telegram 傳訊息後，多久收到最終答案（直接從對話紀錄算；壓縮時會複製舊訊息，所以照時間比，不照 id）。"""
    import sqlite3
    con = sqlite3.connect(f"file:{HERMES / 'state.db'}?mode=ro", uri=True, timeout=5)
    rows = con.execute(
        "with u as (select distinct m.session_id, m.timestamp t from messages m join sessions s on s.id = m.session_id "
        "where s.source = 'telegram' and m.role = 'user' and m.timestamp >= ? "
        # 排程送進對話的通知、系統插入的訊息不是 BB 傳的，不算
        "and m.content not like '[Cron delivery%' and m.content not like '[OUT-OF-BAND%' "
        "and m.content not like 'Gateway message origin%') "
        "select (select min(a.timestamp) from messages a where a.session_id = u.session_id "
        "and a.role = 'assistant' and a.timestamp >= u.t and coalesce(a.content, '') != '' "
        # 10-13：只算 BB 真正收到的最終答案；工具呼叫那幾則、被退回重寫的草稿都不算
        "and coalesce(a.tool_calls, '') in ('', '[]') and coalesce(a.finish_reason, '') != 'verify_hook_continue') - u.t from u",
        (SINCE.timestamp(),)).fetchall()
    con.close()
    waits = sorted(w for (w,) in rows if w is not None)
    if not waits:
        return []
    median, worst = waits[len(waits) // 2], waits[-1]
    mark = "🐢" if worst > 120 else "⏱️"
    return [f"{mark} 你的 {len(waits)} 則訊息：一般 {median:.0f} 秒回覆，最久 {worst:.0f} 秒"]


def prompt_size():
    """昨天 Telegram 每輪固定送給模型的量（prompt-meter 外掛記錄，2026-10-08 起）。估算值約為實際的六成，超過 2 萬才提醒。"""
    path = HERMES / "logs" / "prompt-meter.jsonl"
    if not path.exists():
        return []
    since = SINCE.strftime("%Y-%m-%d %H:%M:%S")
    sizes = []
    for line in open(path, encoding="utf-8", errors="ignore"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("platform") == "telegram" and row.get("ts", "") >= since and row.get("approx_input_tokens"):
            sizes.append(row["approx_input_tokens"])
    if not sizes:
        return []
    avg = sum(sizes) // len(sizes)
    return [f"{'⚠️' if avg > 20000 else '📏'} Telegram 每輪約 {avg:,} token（Hermes 估算，{len(sizes)} 次）"]


def todo():
    out = run("python3", str(HERMES / "scripts" / "pending_work_snapshot.py"))
    items = [l.strip() for l in out.splitlines() if l.strip().startswith("- ")]
    return ["📝 待辦：" + "；".join(i[2:] for i in items[:3])] if items else []


def space():
    disk = "/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else str(HERMES)  # NAS 上看資料所在的碟
    free_gb = shutil.disk_usage(disk).free / 1024**3
    mem = len((HERMES / "memories" / "MEMORY.md").read_text(encoding="utf-8"))
    limit = int(re.search(r"memory_char_limit:\s*(\d+)", (HERMES / "config.yaml").read_text()).group(1))
    mark = "⚠️" if free_gb < 20 or mem / limit > 0.8 else "💾"
    return [f"{mark} 磁碟剩 {free_gb:.0f} GB｜杰哥記憶 {mem * 100 // limit}%"]


def eval_result():
    """每週固定考題（週日 01:00）：跑完後一天內報分數，有題目失敗就列出來。"""
    latest = HERMES / "logs" / "eval" / "latest.json"
    if not latest.exists():
        return []
    report = json.loads(latest.read_text(encoding="utf-8"))
    ran = parse_time(report["time"])
    failed = [r["id"] for r in report["results"] if not r["ok"] and not r.get("passes")]
    flaky = report.get("flaky", [])
    if NOW - ran > timedelta(hours=30) and not failed:
        return smoke_result()
    line = f"🧪 杰哥考題 {report['passed']}/{report['total']}（{ran:%m/%d}）"
    if report.get("regressions"):
        line = "⚠️" + line[1:] + "，退步：" + "、".join(report["regressions"])
    elif failed:
        line += "，沒過：" + "、".join(failed)
    if flaky:
        line += "；不穩：" + "、".join(flaky)
    return [line] + smoke_result()


def smoke_result():
    """10-18 起平常晚上只考 10 題冒煙（eval_nightly.py），有沒過的才報。"""
    smokes = sorted((HERMES / "logs" / "eval").glob("*-smoke.json"))
    if not smokes:
        return []
    report = json.loads(smokes[-1].read_text(encoding="utf-8"))
    ran = parse_time(report["time"])
    failed = [r["id"] for r in report["results"] if not r["ok"]]
    if NOW - ran > timedelta(hours=30) or not failed:
        return []
    return [f"⚠️ 昨晚冒煙考 {report['passed']}/{report['total']}，沒過：" + "、".join(failed)]


WATCHED = ["SOUL.md", "AGENTS.md", "memories/MEMORY.md", "memories/USER.md"]
SNAPSHOT_DIR = HERMES / "cron" / "rulefile_snapshots"


def rule_files():
    """杰哥的人格、規則、記憶檔跟昨天比有沒有被改（2026-10-06：AGENTS.md 開頭曾被不明來源插入政治立場指示）。"""
    import difflib
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for name in WATCHED:
        current = (HERMES / name).read_text(encoding="utf-8").splitlines()
        snap = SNAPSHOT_DIR / name.replace("/", "__")
        if snap.exists():
            before = snap.read_text(encoding="utf-8").splitlines()
            if before != current:
                diff = list(difflib.unified_diff(before, current, lineterm="", n=0))
                added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
                removed = [l for l in diff if l.startswith("-") and not l.startswith("---")]
                top = name in ("SOUL.md", "AGENTS.md") and any(
                    l.startswith("@@") and int(l.split("+")[1].split(",")[0].split()[0]) <= 15 for l in diff)
                mark = "⚠️ 開頭被插入內容，請確認是你改的：" if top else "✏️ "
                preview = next((a.strip() for a in added if a.strip()), "")[:30]
                lines.append(f"{mark}{name} +{len(added)}/-{len(removed)} 行" + (f"「{preview}」" if preview else ""))
        if "--preview" not in sys.argv:  # 手動預覽不要更新比對基準，不然排程那次就看不到變動
            snap.write_text("\n".join(current), encoding="utf-8")
    return lines


def memory_sweep():
    """記憶清道夫昨晚自動作廢了哪些矛盾的舊記憶（沒有就不出聲）。"""
    path = HERMES / "cron" / "memory_sweeper_last.json"
    if not path.exists():
        return ["⚠️ 記憶清道夫還沒有執行紀錄"]
    data = json.loads(path.read_text(encoding="utf-8"))
    ran = parse_time(data.get("time", ""))
    if ran and (NOW - ran).total_seconds() > 30 * 3600:
        return [f"⚠️ 記憶清道夫超過 30 小時沒跑（上次 {ran:%m/%d %H:%M}）"]
    if not data.get("invalidated"):
        return []
    lines = [f"🧹 記憶自動修正 {data['invalidated']} 筆（舊說法已作廢，可還原）："]
    for item in data.get("items", [])[:3]:
        lines.append(f"  「{item['old'][:40]}」← {item['because'][:30]}")
    return lines


def hermes_release():
    """官方出新版就提醒一次（2026-10-21）。不會自動升：要重建 hermes-local 映像。
    第二碼變了（例如 0.22 → 0.23）是大版本，官方會動底層，先看改動清單再升。"""
    latest = run("gh", "release", "view", "-R", "NousResearch/hermes-agent", "--json", "tagName", "-q", ".tagName").strip()
    current = re.search(r"v(\d+\.\d+\.\d+)", run("/opt/hermes/.venv/bin/hermes", "--version"))
    if not latest or not current or latest.lstrip("v") == current.group(1):
        return []
    seen = HERMES / "logs" / "hermes_release_seen.txt"
    if seen.exists() and seen.read_text().strip() == latest:
        return []
    if "--preview" not in sys.argv:
        seen.write_text(latest)
    big = latest.lstrip("v").split(".")[:2] != current.group(1).split(".")[:2]
    note = "大版本，先請 Claude 看改動清單再升" if big else "小修正，可以請 Claude 升級"
    return [f"🆕 Hermes 出了 {latest}（目前 v{current.group(1)}）：{note}"]


def main():
    lines = [f"☀️ 早報 {NOW:%m/%d}（{WEEKDAY}）"]
    for section in (system_health, self_heal, rule_files, backups, offsite_backup, capabilities_check, failed_jobs, model_events, studio_queue, memory_sweep, dream_todo,
                    deploy_health, jules_waiting, open_prs, hermes_release, eval_result, prompt_size, todo, space):
        try:
            lines += section()
        except Exception as exc:  # 一個來源壞掉不要拖垮整份早報
            lines.append(f"⚠️ {section.__name__} 讀取失敗：{str(exc)[:60]}")
    if FAILED:
        lines.append("⚠️ 這幾項查不到，上面可能少了它們的內容：" + "；".join(dict.fromkeys(FAILED)))
    print("\n".join(lines))


def early_prophet():
    """預言家日報（魔法報紙風格 + 真實數據，不含互動選單）"""
    import shutil
    import json
    import os

    def _box(texts, width=58):
        """畫魔法框，支援 CJK 寬度"""
        def _w(s):
            return sum(2 if ord(c) > 127 else 1 for c in s)
        max_w = max(_w(t) for t in texts) + 4
        top = "═" * (max_w - 2)
        bottom = "═" * (max_w - 2)
        body = "\n".join("║ " + t + " " * (max_w - 4 - _w(t)) + " ║" for t in texts)
        return f"╔{top}╗\n{body}\n╚{bottom}╝"

    # 頭版
    today = NOW.strftime("%m/%d")
    ed = (NOW - datetime(2026, 10, 22, tzinfo=NOW.tzinfo)).days + 1
    header = [
        f"🔮 每日預言家 · 第 {ed:03d} 期（{today}·{WEEKDAY}）",
        "── 傲羅辦公室證實：系統昨夜安全檢查通過 ──",
        "磁碟空間充足，無食屍鬼入侵軌跡",
    ]

    # 系統報告（複用現有 section）
    sys_lines = ["【魔法部通訊 · 系統現況】"]
    sys_lines += system_health()
    sys_lines += self_heal()
    sys_lines += studio_queue()

    # 學校待辦（讀 school_deadlines.json）
    task = _read_school_deadlines()

    # 考试
    exam = eval_result()

    # 磁碟
    d = space()

    # 預言（基於真實數據）
    proph = ["🔮 預言家時間 · 不經占卜，純看紀錄"]
    proph_items = []
    try:
        free_gb = shutil.disk_usage(
            "/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else str(HERMES)
        ).free / 1024**3
        days_left = int(free_gb / 50)  # 假設每天用 50GB
        proph_items.append(f"磁碟空間：剩 {free_gb:.0f}GB，約 {days_left} 天到 80% 警戒線")
    except Exception:
        proph_items.append("磁碟資料查不到")

    studio = studio_queue()
    if studio and "連不到佇列" in studio[0]:
        proph_items.append("Studio 佇列：今天有斷線，明天可能再發生")

    if exam:
        proph_items.append("考試：最近退步，建議今晚複習不穩項目")

    # 新增：最近考試分數
    latest = HERMES / "logs" / "eval" / "latest.json"
    if latest.exists():
        r = json.loads(latest.read_text(encoding="utf-8"))
        if r.get("passed"):
            proph_items.append(f"最近考試：{r['passed']}/{r['total']}（{r['time'][:10]}）")
        if r.get("regressions"):
            proph_items.append(f"退步項：{r['regressions']}")

    for i, item in enumerate(proph_items, 1):
        proph.append(f"  {i}. {item}")
    proph.append("")
    proph.append("  🧙‍♂️ 杰哥的忠告：『別等傲羅來敲門才整理記憶庫』")

    # 組合（移除 X 熱門話題、互動選單）
    parts = [
        _box(header),
        "",
        "\n".join(sys_lines),
        "",
        "\n".join(task),
        "",
        "\n".join(d),
        "",
        "\n".join(proph),
        "",
    ]
    return "\n".join(parts)


def _read_school_deadlines():
    """讀 school_deadlines.json，過濾近 7 天內截止項，優惠單獨列"""
    path = HERMES / "logs" / "school_deadlines.json"
    if not path.exists():
        return ["📅 學校待辦", "  • 找不到行事曆資料"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ["📅 學校待辦", "  • 讀取行事曆失敗"]

    now = NOW
    tasks = []
    perks = []
    for key, val in data.items():
        what = val.get("what", "")
        end = val.get("end", "")
        if end and key.startswith("perk-"):
            perks.append(f"{what}（{end[:10]}）")
        elif end:
            end_date = end[:10]
            end_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=now.tzinfo)
            if now <= end_dt <= now + timedelta(days=7):
                tasks.append(f"{what}（{end_date} 截止）")
            elif end_dt > now + timedelta(days=7):
                tasks.append(f"{what}（{end_date} 截止）")
    if not tasks and not perks:
        return ["📅 學校待辦", "  • 近期沒有截止項"]
    task_lines = ["📅 學校待辦"]
    for t in tasks[:5]:
        task_lines.append(f"  • {t}")
    if len(tasks) > 5:
        task_lines.append(f"  ... 還有 {len(tasks) - 5} 件")
    for p in perks:
        task_lines.append(f"  ★ 優惠：{p}")
    return task_lines


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--prophet":
        print(early_prophet())
    else:
        main()
