#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re
from urllib.parse import urlparse

# --- 权限配置 (GitHub Secrets) ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# --- 接口配置 ---
RAW_URLS = os.environ.get("SOURCE_URLS", "")

if not RAW_URLS:
    print("❌ 错误：SOURCE_URLS 为空！")
    exit(1)

SOURCE_URLS = [u.strip() for u in RAW_URLS.replace('，', ',').split(',') if u.strip()]
HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ips_audit():
    source_stats = {}
    ip_map = {}
    
    # 自动编号命名：接口 01, 接口 02...
    for index, url in enumerate(SOURCE_URLS, 1):
        name = f"接口 {index:02d}"
        
        try:
            res = requests.get(url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            if res.status_code == 200:
                found_ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text)
                ips = [ip for ip in found_ips if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                
                source_stats[name] = len(ips)
                for ip in ips:
                    if ip not in ip_map:
                        ip_map[ip] = []
                    ip_map[ip].append(name)
            else:
                source_stats[name] = f"Error:{res.status_code}"
        except:
            source_stats[name] = "Timeout"

    unique_ips = list(ip_map.keys())
    
    # 统计重复详情
    dup_details = []
    total_raw_count = 0
    for ip, sources in ip_map.items():
        total_raw_count += len(sources)
        if len(sources) > 1:
            # sources 已经变成了 ["接口 01", "接口 03"]
            src_list = ", ".join(sorted(set(sources)))
            dup_details.append(f"`{ip}` ({src_list})")

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
            return True, "OK", res_j
        return False, "API Error", None
    except:
        return False, "Network Error", None

def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=15)
    except:
        pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("❌ 配置缺失")
        return

    ips, audit = get_ips_audit()
    if not ips:
        print("⚠️ 无有效 IP")
        return

    # --- 1. 构造报告 (接口匿名化) ---
    report = [f"📊 *节点审计报告* (唯一总数: {audit['final']})"]
    for src, count in audit['sources'].items():
        report.append(f"• `{src}` → *{count}* IP")
    
    report.append(f"\n原始累计: {audit['raw']} | **生效: {audit['final']}**")
    
    # --- 2. 插入重复详情 ---
    if audit['dup_list']:
        report.append(f"\n⚠️ *发现 {audit['dup_count']} 个重复项*:")
        for item in audit['dup_list'][:10]: # 仅显示前10条防刷屏
            report.append(f"└ {item}")
    
    report.append("\n" + "—" * 15 + "\n")

    # --- 3. 域名解析 (仅展示二级名称) ---
    domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    for domain in domains:
        # 【关键修复】提取二级名称，如 cdn.example.com 变为 cdn
        short_name = domain.split('.')[0]
        
        success, msg, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success:
            report.append(f"❌ **{short_name}**: {msg}")
            continue
            
        old_recs = sorted(res.get('result', []), key=lambda x: x['id'])
        oc, nc = len(old_recs), len(ips)
        ops = {"u": 0, "a": 0, "d": 0}

        for i in range(max(nc, oc)):
            if i < nc and i < oc:
                if old_recs[i]['content'] != ips[i]:
                    ok, _, _ = cf_api("PUT", f"dns_records/{old_recs[i]['id']}", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                    if ok: ops["u"]+=1
            elif i < nc:
                ok, _, _ = cf_api("POST", "dns_records", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                if ok: ops["a"]+=1
            elif i < oc:
                ok, _, _ = cf_api("DELETE", f"dns_records/{old_recs[i]['id']}")
                if ok: ops["d"]+=1
            time.sleep(0.3)

        report.append(f"🌐 *目标域*: `{short_name}`\n- 更新IP: `{ops['u']}` | 新增IP: `{ops['a']}` | 删除IP: `{ops['d']}`")

    full_content = "\n".join(report)
    
    # --- 4. 发送通知 ---
    if PUSHPLUS_TOKEN:
        requests.post('http://www.pushplus.plus/send', json={"token": PUSHPLUS_TOKEN, "title": "CFIP 解析报告", "content": full_content, "template": "markdown"})

    send_telegram(f"🚀 *任务自动同步完成*\n\n{full_content}")

if __name__ == '__main__':
    main()
