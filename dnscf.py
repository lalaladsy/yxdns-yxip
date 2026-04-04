#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 权限配置 (全部从 GitHub Secrets 读取) ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# --- 接口配置 (仅从变量读取) ---
RAW_URLS = os.environ.get("SOURCE_URLS", "")

# 核心校验：如果没有设置接口变量，直接终止
if not RAW_URLS:
    print("❌ 错误：检测到 SOURCE_URLS 变量为空！请在 GitHub Secrets 中配置接口地址。")
    exit(1)

# 将逗号分隔的字符串转换为列表
SOURCE_URLS = [u.strip() for u in RAW_URLS.replace('，', ',').split(',') if u.strip()]

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ips_audit():
    source_stats, all_raw_ips = {}, []
    for url in SOURCE_URLS:
        # 提取 URL 的一部分作为报告中的名称
        name = url.split('/')[-1] if len(url.split('/')[-1]) > 3 else url.split('/')[-2]
        try:
            res = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
            if res.status_code == 200:
                ips = [ip for ip in re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text) 
                       if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
            else:
                source_stats[name] = f"Err:{res.status_code}"
        except:
            source_stats[name] = "Timeout"
    unique_ips = list(dict.fromkeys(all_raw_ips))
    return unique_ips, {"sources": source_stats, "raw": len(all_raw_ips), "dup": len(all_raw_ips) - len(unique_ips), "final": len(unique_ips)}

def cf_api(method, endpoint, data=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        if res.status_code == 200 and res_j.get('success'):
            return True, "OK", res_j
        msg = res_j.get('errors', [{}])[0].get('message', 'CF API Error')
        return False, msg, None
    except Exception as e:
        return False, str(e), None

def send_telegram(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        return
    safe_text = text.replace("_", "\\_")
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": safe_text, "parse_mode": "Markdown"}, timeout=15)
    except:
        pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("❌ 缺少核心配置 (CF_API_TOKEN/ZONE_ID/DNS_NAME)")
        return

    ips, audit = get_ips_audit()
    if not ips:
        print("⚠️ 未能从提供的接口中抓取到任何有效 IP")
        return

    report = [f"📊 *节点审计报告* (总数: {audit['final']})"]
    for src, count in audit['sources'].items():
        report.append(f"• `{src}` → *{count}* IP")
    report.append(f"\n获取IP: {audit['raw']} | 重复IP: {audit['dup']} | **生效IP: {audit['final']}**\n\n---")

    domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    for domain in domains:
        success, msg, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success:
            report.append(f"❌ **{domain}**: {msg}")
            continue
            
        old_recs = sorted(res.get('result', []), key=lambda x: x['id'])
        oc, nc = len(old_recs), len(ips)
        ops = {"u": 0, "a": 0, "d": 0, "e": 0}

        for i in range(max(nc, oc)):
            if i < nc and i < oc:
                if old_recs[i]['content'] != ips[i]:
                    ok, _, _ = cf_api("PUT", f"dns_records/{old_recs[i]['id']}", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                    if ok: ops["u"]+=1
                    else: ops["e"]+=1
            elif i < nc:
                ok, _, _ = cf_api("POST", "dns_records", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                if ok: ops["a"]+=1
                else: ops["e"]+=1
            elif i < oc:
                ok, _, _ = cf_api("DELETE", f"dns_records/{old_recs[i]['id']}")
                if ok: ops["d"]+=1
                else: ops["e"]+=1
            time.sleep(0.3)

        report.append(f"🌐 *目标域名*: `{domain}`\n- 状态: 更新IP解析 `{ops['u']}` | 新增IP解析 `{ops['a']}` | 删除IP解析 `{ops['d']}`\n")

    full_content = "\n".join(report)
    
    if PUSHPLUS_TOKEN:
        requests.post('http://www.pushplus.plus/send', json={"token": PUSHPLUS_TOKEN, "title": "CF 优选IP解析报告", "content": full_content, "template": "markdown"})

    send_telegram(f"🚀 *CF优选IP 自动解析报告*\n\n{full_content}")

if __name__ == '__main__':
    main()
