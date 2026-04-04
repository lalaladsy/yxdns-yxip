#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re
from urllib.parse import urlparse

# --- 权限配置 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# --- 接口配置 ---
RAW_URLS = os.environ.get("SOURCE_URLS", "")

if not RAW_URLS:
    print("❌ 错误：SOURCE_URLS 变量未设置！")
    exit(1)

SOURCE_URLS = [u.strip() for u in RAW_URLS.replace('，', ',').split(',') if u.strip()]
HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ips_audit():
    source_stats, ip_map = {}, {}
    for index, url in enumerate(SOURCE_URLS, 1):
        name = f"接口 {index:02d}"
        try:
            res = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
            if res.status_code == 200:
                found_ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text)
                ips = [ip for ip in found_ips if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                source_stats[name] = len(ips)
                for ip in ips:
                    if ip not in ip_map: ip_map[ip] = []
                    ip_map[ip].append(name)
            else:
                source_stats[name] = f"Err:{res.status_code}"
        except:
            source_stats[name] = "Timeout"

    unique_ips = list(ip_map.keys())
    dup_details = []
    total_raw_count = 0
    for ip, sources in ip_map.items():
        total_raw_count += len(sources)
        if len(sources) > 1:
            src_list = ", ".join(sorted(set(sources)))
            dup_details.append(f"{ip} ({src_list})")

    return unique_ips, {
        "sources": source_stats, 
        "raw": total_raw_count, 
        "dup_count": total_raw_count - len(unique_ips), 
        "dup_list": dup_details, 
        "final": len(unique_ips)
    }

def cf_api(method, endpoint, data=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        if res.status_code == 200 and res_j.get('success'):
            return True, res_j
        return False, None
    except:
        return False, None

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return
    ips, audit = get_ips_audit()
    if not ips: return

    domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    results = []

    for domain in domains:
        short_name = domain.split('.')[0]
        success, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success: continue
            
        old_recs = sorted(res.get('result', []), key=lambda x: x['id'])
        oc, nc = len(old_recs), len(ips)
        ops = {"u": 0, "a": 0, "d": 0}

        for i in range(max(nc, oc)):
            if i < nc and i < oc:
                if old_recs[i]['content'] != ips[i]:
                    ok, _ = cf_api("PUT", f"dns_records/{old_recs[i]['id']}", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                    if ok: ops["u"]+=1
            elif i < nc:
                ok, _ = cf_api("POST", "dns_records", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                if ok: ops["a"]+=1
            elif i < oc:
                ok, _ = cf_api("DELETE", f"dns_records/{old_recs[i]['id']}")
                if ok: ops["d"]+=1
            time.sleep(0.2)
        results.append({"name": short_name, "u": ops["u"], "a": ops["a"], "d": ops["d"]})

    # ==========================================
    # 美化版逻辑 A：Telegram (极客风)
    # ==========================================
    if TG_BOT_TOKEN and TG_CHAT_ID:
        tg_text = [
            f"🚀 *CF IP-AutoSync Terminal*\n",
            f"💎 *Audit Strategy*: `Unique:{audit['final']}`",
            f"📈 *Data Source Status*:"
        ]
        for src, count in audit['sources'].items():
            tg_text.append(f" ├ `{src}` ❯ `{count}` IP")
        
        if audit['dup_list']:
            tg_text.append(f"\n⚠️ *Duplication Found* (`{audit['dup_count']}`):")
            for item in audit['dup_list'][:8]: 
                tg_text.append(f" └ `{item}`")
            
        tg_text.append("\n" + "—" * 15)
        for r in results:
            tg_text.append(f"🌐 *DNS: {r['name']}*")
            tg_text.append(f" `Update:{r['u']}` | `Add:{r['a']}` | `Del:{r['d']}`")

        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TG_CHAT_ID, "text": "\n".join(tg_text), "parse_mode": "Markdown"})

    # ==========================================
    # 美化版逻辑 B：PushPlus (卡片风)
    # ==========================================
    if PUSHPLUS_TOKEN:
        pp_text = [
            f"## 🛠️ CFIP 解析审计报告",
            f"**唯一生效**: `{audit['final']}` | **原始抓取**: `{audit['raw']}`\n",
            "---"
        ]
        
        # 接口统计
        for src, count in audit['sources'].items():
            pp_text.append(f"- **{src}**：`{count}` 条记录")
        
        # 重复项采用引用块，增加视觉深度
        if audit['dup_list']:
            pp_text.append(f"\n### ⚠️ 重复项详情 (Total: {audit['dup_count']})")
            for item in audit['dup_list'][:10]:
                pp_text.append(f"> {item}")
        
        pp_text.append("\n" + "---" + "\n")
        
        # 域名执行结果
        for r in results:
            pp_text.append(f"#### 🌐 目标域：`{r['name']}`")
            pp_text.append(f"✅ 更新: **{r['u']}** | ➕ 新增: **{r['a']}** | ➖ 删除: **{r['d']}**\n")

        requests.post('http://www.pushplus.plus/send', 
                      json={"token": PUSHPLUS_TOKEN, "title": "CFIP 自动化报告", 
                            "content": "\n\n".join(pp_text), "template": "markdown"})

if __name__ == '__main__':
    main()
