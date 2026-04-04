#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 环境变量配置 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")  
CF_IP_URL = os.environ.get("CF_IP_URL")      
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def extract_ips(text):
    """智能正则提取 IPv4"""
    pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    found = re.findall(pattern, text)
    return [ip for ip in found if all(0 <= int(p) <= 255 for p in ip.split('.'))]

def get_all_ips_with_stats():
    """多接口抓取并进行详细统计"""
    if not CF_IP_URL: return [], {}
    urls = [u.strip() for u in CF_IP_URL.split(',') if u.strip()]
    source_stats, all_raw_ips = {}, []
    
    for url in urls:
        name = url.split('//')[-1].split('/')[0][:20]
        try:
            res = requests.get(url, timeout=12)
            if res.status_code == 200:
                ips = extract_ips(res.text)
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
            else: source_stats[name] = f"ERR({res.status_code})"
        except: source_stats[name] = "TIMEOUT"
    
    unique_ips, seen = [], set()
    repeat_count = 0
    for ip in all_raw_ips:
        if ip not in seen:
            unique_ips.append(ip)
            seen.add(ip)
        else:
            repeat_count += 1
            
    return unique_ips, {"sources": source_stats, "raw": len(all_raw_ips), "repeat": repeat_count, "final": len(unique_ips)}

def modify_dns(method, name, ip=None, record_id=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    if record_id: url += f'/{record_id}'
    data = {'type': 'A', 'name': name, 'content': ip, 'ttl': 60, 'proxied': False}
    try:
        if method == "POST": res = requests.post(url, headers=HEADERS, json=data, timeout=10)
        elif method == "PUT": res = requests.put(url, headers=HEADERS, json=data, timeout=10)
        elif method == "DELETE": res = requests.delete(url, headers=HEADERS, timeout=10)
        return res.status_code == 200
    except: return False

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME, CF_IP_URL]): return
    unique_ips, ip_stats = get_all_ips_with_stats()
    if not unique_ips: return

    # --- 构造专业 HTML ---
    html = ["<div style='font-family:Segoe UI,Microsoft YaHei,sans-serif; background:#f9f9f9; padding:15px; border-radius:10px;'>"]
    
    # 顶部状态卡片
    html.append(f"""
    <div style='background:#fff; border-left:5px solid #007bff; padding:10px; margin-bottom:15px; box-shadow:0 2px 5px rgba(0,0,0,0.05);'>
        <h3 style='margin:0 0 10px 0; color:#333;'>📡 优选数据源状态</h3>
    """)
    for src, count in ip_stats['sources'].items():
        html.append(f"<div style='font-size:13px; color:#666;'>• <code>{src}</code> → <b style='color:#007bff;'>{count}</b> IP</div>")
    html.append(f"""
        <div style='margin-top:10px; padding-top:10px; border-top:1px solid #eee; font-size:14px;'>
            原始汇总: <b>{ip_stats['raw']}</b> | 自动去重: <b style='color:#e67e22;'>{ip_stats['repeat']}</b> | <b>最终可用: <span style='color:#27ae60;'>{ip_stats['final']}</span></b>
        </div>
    </div>
    """)

    detail_table = ["状态 序号 | IP 地址         | 目标域名", "-"*45]
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    
    for domain in target_domains:
        curr_res = requests.get(f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records?type=A&name={domain}&per_page=100', headers=HEADERS).json()
        old_records = sorted(curr_res.get('result', []), key=lambda x: x['id'])
        old_count, new_count = len(old_records), len(unique_ips)
        stats = {"upd": 0, "add": 0, "del": 0, "ok": 0}
        
        for i in range(max(new_count, old_count)):
            symbol, target_ip = "  ", (unique_ips[i] if i < new_count else "DELETED")
            if i < new_count and i < old_count:
                if old_records[i]['content'] != unique_ips[i]:
                    if modify_dns("PUT", domain, unique_ips[i], old_records[i]['id']):
                        stats["upd"] += 1; symbol = "🔄"
                else: stats["ok"] += 1; symbol = "✅"
            elif i < new_count:
                if modify_dns("POST", domain, unique_ips[i]):
                    stats["add"] += 1; symbol = "➕"
            elif i < old_count:
                if modify_dns("DELETE", domain, record_id=old_records[i]['id']):
                    stats["del"] += 1; symbol = "➖"
            
            detail_table.append(f"{symbol}  {i+1:02}  | {target_ip:<15} | {domain}")
            time.sleep(0.2)

        # 域名变动卡片
        html.append(f"""
        <div style='background:#fff; border-left:5px solid #27ae60; padding:10px; margin-bottom:15px; box-shadow:0 2px 5px rgba(0,0,0,0.05);'>
            <h4 style='margin:0 0 8px 0; color:#2c3e50;'>🌐 {domain}</h4>
            <div style='font-size:13px; display:grid; grid-template-columns: 1fr 1fr; gap:5px;'>
                <span>获取 IP: <b>{new_count}</b></span>
                <span>原有坑位: <b>{old_count}</b></span>
                <span style='color:#2980b9;'>更新: {stats['upd']}</span>
                <span style='color:#27ae60;'>新增: {stats['add']}</span>
                <span style='color:#c0392b;'>删除: {stats['del']}</span>
                <span style='color:#7f8c8d;'>保持: {stats['ok']}</span>
            </div>
            <div style='margin-top:8px; font-weight:bold; color:#27ae60;'>最终状态: {new_count} 条解析已生效</div>
        </div>
        """)

    # 折叠详情
    html.append(f"""
    <details style='margin-top:10px;'>
        <summary style='color:#007bff; font-size:14px; cursor:pointer; font-weight:bold;'>▶ 查看底层解析对照表 (Console)</summary>
        <pre style='background:#2d3436; color:#dfe6e9; padding:12px; border-radius:5px; font-family:Consolas,monaco,monospace; font-size:11px; margin-top:10px; overflow-x:auto; line-height:1.5;'>
{chr(10).join(detail_table)}
        </pre>
    </details>
    </div>
    """)
    
    requests.post('http://www.pushplus.plus/send', json={
        "token": PUSHPLUS_TOKEN,
        "title": "Cloudflare DNS 自动化运维报告",
        "content": "".join(html),
        "template": "html"
    })

if __name__ == '__main__':
    main()
