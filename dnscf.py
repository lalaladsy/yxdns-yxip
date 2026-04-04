#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re
from urllib.parse import urlparse

# --- 权限配置 (全部从 GitHub Secrets 读取) ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")

# --- 接口配置 ---
RAW_URLS = os.environ.get("SOURCE_URLS", "")

if not RAW_URLS:
    print("❌ 错误：检测到 SOURCE_URLS 变量为空！")
    exit(1)

# 处理中文逗号并转为列表
SOURCE_URLS = [u.strip() for u in RAW_URLS.replace('，', ',').split(',') if u.strip()]
HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ips_audit():
    source_stats = {}
    ip_map = {}  # 格式: {"IP": ["来源名称"]}
    
    for url in SOURCE_URLS:
        parsed = urlparse(url)
        domain = parsed.netloc
        path = parsed.path.strip('/')
        
        # 优化命名逻辑：如果存在特定路径（如 cu/cmcc），则包含路径以区分同域名接口
        if path and len(path) > 1:
            name = f"{domain}/{path.split('/')[-1]}"
        else:
            name = domain if domain else url.split('/')[-1]

        try:
            # 针对 GitHub Raw 等接口增加 User-Agent
            res = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            if res.status_code == 200:
                # 匹配 IPv4 正则
                found_ips = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text)
                ips = [ip for ip in found_ips if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                
                source_stats[name] = len(ips)
                for ip in ips:
                    if ip not in ip_map:
                        ip_map[ip] = []
                    ip_map[ip].append(name)
            else:
                source_stats[name] = f"Err:{res.status_code}"
        except Exception as e:
            source_stats[name] = "Timeout/Error"

    unique_ips = list(ip_map.keys())
    
    # 分析重复项详情
    dup_details = []
    total_raw_count = 0
    for ip, sources in ip_map.items():
        total_raw_count += len(sources)
        if len(sources) > 1:
            # 去重并排序来源
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
        msg = res_j.get('errors', [{}])[0].get('message', 'CF API Error')
        return False, msg, None
    except Exception as e:
        return False, str(e), None

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
        print("❌ 缺少核心配置 (CF_API_TOKEN/ZONE_ID/DNS_NAME)")
        return

    ips, audit = get_ips_audit()
    if not ips:
        print("⚠️ 未能抓取到任何有效 IP")
        return

    # --- 构造报告 ---
    report = [f"📊 *IP节点审计报告* (唯一总数: {audit['final']})"]
    for src, count in audit['sources'].items():
        report.append(f"• `{src}` → *{count}* IP")
    
    report.append(f"\n原始累计: {audit['raw']} | **生效: {audit['final']}**")
    
    # 插入重复详情
    if audit['dup_list']:
        report.append(f"\n⚠️ *发现 {audit['dup_count']} 个重复项*:")
        # 限制显示前 15 条，避免 TG 消息过长触发限制
        for item in audit['dup_list'][:15]:
            report.append(f"└ {item}")
        if len(audit['dup_list']) > 15:
            report.append(f"└ ...等共 {len(audit['dup_list'])} 组重复")
    
    report.append("\n" + "—" * 20)

    # --- 域名更新 ---
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

        report.append(f"🌐 *目标域名*: `{domain}`\n- 状态: 更新IP `{ops['u']}` | 新增IP `{ops['a']}` | 删除IP `{ops['d']}`")

    full_content = "\n".join(report)
    
    if PUSHPLUS_TOKEN:
        requests.post('http://www.pushplus.plus/send', json={"token": PUSHPLUS_TOKEN, "title": "CFIP 解析报告", "content": full_content, "template": "markdown"})

    send_telegram(f"🚀 *CF 自动DNS解析完成*\n\n{full_content}")

if __name__ == '__main__':
    main()
