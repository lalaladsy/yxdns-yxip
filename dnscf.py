#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re
from urllib.parse import urlparse

# --- 权限配置 (请在 GitHub Secrets 中配置) ---
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
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("❌ 核心配置缺失")
        return

    ips, audit = get_ips_audit()
    if not ips:
        print("⚠️ 未抓取到有效 IP")
        return

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
        
        # 计算当前该域名最终拥有的解析记录总数
        current_total = oc - ops["d"] + ops["a"]
        results.append({"name": short_name, "u": ops["u"], "a": ops["a"], "d": ops["d"], "total": current_total})

    # ==========================================
    # 🚀 TELEGRAM 推送 (运维看板 - 中文版)
    # ==========================================
    if TG_BOT_TOKEN and TG_CHAT_ID:
        tg_text = [
            f"🚀 *Cloudflare 解析同步终端*",
            f"━━━━━━━━━━━━━━━━━━",
            f"📊 *运行数据统计*",
            f" ├ ✅ 可用 IP: `{audit['final']}`",
            f" └ 📥 抓取 IP: `{audit['raw']}`",
            f"\n📁 *接口快照*:"
        ]
        
        src_keys = list(audit['sources'].keys())
        for i, src in enumerate(src_keys):
            sym = " └──" if i == len(src_keys) - 1 else " ├──"
            tg_text.append(f"{sym} `{src}` ❯ `{audit['sources'][src]}` IP")
        
        if audit['dup_list']:
            tg_text.append(f"\n⚠️ *重复项过滤* (`{audit['dup_count']}` 组):")
            for item in audit['dup_list'][:20]: 
                tg_text.append(f" └ `{item}`")
            
        tg_text.append(f"\n📡 *解析执行状态*:")
        for r in results:
            tg_text.append(f" ├── 域名: *{r['name']}*")
            # 在后面增加了“当前”统计
            tg_text.append(f" └── 🟢更新:`{r['u']}`|🔵新增:`{r['a']}`|🔴删除:`{r['d']}`|✨当前:`{r['total']}`")

        tg_text.append(f"━━━━━━━━━━━━━━━━━━")
        tg_text.append(f"⏰ 执行时间: `{time.strftime('%H:%M:%S')}`")

        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TG_CHAT_ID, "text": "\n".join(tg_text), "parse_mode": "Markdown"})

    # ==========================================
    # 📋 PUSHPLUS 推送 (经典表情版)
    # ==========================================
    if PUSHPLUS_TOKEN:
        pp_text = [
            f"📊 **节点审计报告** (可用总数: {audit['final']})\n",
        ]
        for src, count in audit['sources'].items():
            pp_text.append(f"• `{src}` → **{count}** IP")
        
        pp_text.append(f"\n📥 抓取 IP: {audit['raw']} | ✅ **可用 IP: {audit['final']}**\n")
        
        if audit['dup_list']:
            pp_text.append(f"⚠️ **发现 {audit['dup_count']} 个重复项**:")
            for item in audit['dup_list'][:20]:
                pp_text.append(f"└ {item}")
        
        pp_text.append("\n" + "—" * 15 + "\n")
        
        for r in results:
            pp_text.append(f"🌐 **目标域**: `{r['name']}`")
            # 同样在 PushPlus 增加了当前总计展示
            pp_text.append(f"✅ 更新: {r['u']} | ➕ 新增: {r['a']} | ➖ 删除: {r['d']} | ✨ 当前: {r['total']}\n")

        requests.post('http://www.pushplus.plus/send', 
                      json={"token": PUSHPLUS_TOKEN, "title": "CFIP 解析报告", 
                            "content": "\n\n".join(pp_text), "template": "markdown"})

if __name__ == '__main__':
    main()
