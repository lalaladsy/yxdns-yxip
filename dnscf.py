#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 核心配置 (通过 GitHub Secrets 获取) ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")  # 多个域名请用英文逗号分隔
CF_IP_URL = os.environ.get("CF_IP_URL")      # 多个接口请用英文逗号分隔
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {
    'Authorization': f'Bearer {CF_API_TOKEN}',
    'Content-Type': 'application/json'
}

def extract_ips(text):
    """提取文本中所有合法的 IPv4 地址"""
    pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    found = re.findall(pattern, text)
    return [ip for ip in found if all(0 <= int(p) <= 255 for p in ip.split('.'))]

def get_all_ips_with_stats():
    """执行多源负载均衡抓取并统计"""
    if not CF_IP_URL: return [], {}
    urls = [u.strip() for u in CF_IP_URL.split(',') if u.strip()]
    source_stats, all_raw_ips = {}, []
    
    for url in urls:
        # 截取接口域名作为标识
        name = url.split('//')[-1].split('/')[0][:20]
        try:
            res = requests.get(url, timeout=12)
            if res.status_code == 200:
                ips = extract_ips(res.text)
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
            else: source_stats[name] = f"ERR({res.status_code})"
        except: source_stats[name] = "TIMEOUT"
    
    # 智能去重逻辑
    unique_ips, seen = [], set()
    repeat_count = 0
    for ip in all_raw_ips:
        if ip not in seen:
            unique_ips.append(ip)
            seen.add(ip)
        else:
            repeat_count += 1
            
    return unique_ips, {
        "sources": source_stats, 
        "raw": len(all_raw_ips), 
        "repeat": repeat_count, 
        "final": len(unique_ips)
    }

def modify_dns(method, name, ip=None, record_id=None):
    """Cloudflare DNS 调度执行器"""
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
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME, CF_IP_URL]):
        print("Error: 环境变量配置不完整")
        return

    unique_ips, ip_stats = get_all_ips_with_stats()
    if not unique_ips:
        print("Notice: 未捕获到有效 IP，任务终止")
        return

    # --- 构造看板组件 ---
    # 组件 1: 接口贡献分析
    source_html = ""
    for src, count in ip_stats['sources'].items():
        source_html += f"<div style='background:#eef6ff;border:1px solid #d0e7ff;padding:4px 8px;border-radius:4px;font-size:12px;margin-right:5px;margin-bottom:5px;display:inline-block;'>📡 <code>{src}</code>: <b style='color:#007bff;'>{count}</b></div>"

    # 组件 2: 审计日志初始化
    audit_log = ["STATUS  IDX | IP_ADDRESS      | TARGET_DOMAIN", "-"*45]
    domain_cards = ""
    
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    for domain in target_domains:
        # 获取当前解析状态
        curr_url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records?type=A&name={domain}&per_page=100'
        old_records = sorted(requests.get(curr_url, headers=HEADERS).json().get('result', []), key=lambda x: x['id'])
        
        old_count, new_count = len(old_records), len(unique_ips)
        stats = {"upd": 0, "add": 0, "del": 0, "ok": 0}
        
        # 弹性伸缩核心逻辑
        for i in range(max(new_count, old_count)):
            symbol, target_ip = "[SKIPPED]", (unique_ips[i] if i < new_count else "DELETED")
            
            if i < new_count and i < old_count:
                if old_records[i]['content'] != unique_ips[i]:
                    if modify_dns("PUT", domain, unique_ips[i], old_records[i]['id']):
                        stats["upd"] += 1; symbol = "[PATCHED]"
                else: stats["ok"] += 1; symbol = "[SYNCED ]"
            elif i < new_count:
                if modify_dns("POST", domain, unique_ips[i]):
                    stats["add"] += 1; symbol = "[DEPLOY ]"
            elif i < old_count:
                if modify_dns("DELETE", domain, record_id=old_records[i]['id']):
                    stats["del"] += 1; symbol = "[REMOVED]"
            
            # 记录审计日志
            audit_log.append(f"{symbol} {i+1:02} | {target_ip:<15} | {domain}")
            time.sleep(0.2)

        # 构造域名状态卡片
        domain_cards += f"""
        <div style='border:1px solid #eee;padding:12px;border-radius:8px;margin-bottom:10px;background:#fff;'>
            <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'>
                <b style='font-size:14px;color:#2c3e50;'>🌐 {domain}</b>
                <span style='background:#27ae60;color:#fff;font-size:10px;padding:2px 8px;border-radius:10px;'>ACTIVE</span>
            </div>
            <div style='font-size:12px;color:#666;'>
                弹性跨度: <code>{old_count}</code> ➔ <b style='color:#007bff;'>{new_count}</b><br>
                执行统计: <span style='color:#2980b9;'>更新({stats['upd']})</span> | <span style='color:#27ae60;'>新增({stats['add']})</span> | <span style='color:#c0392b;'>删除({stats['del']})</span> | 保持({stats['ok']})
            </div>
        </div>
        """

    # --- 包装终极推送模板 ---
    final_html = f"""
    <div style="font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f8f9fa;padding:15px;border-radius:12px;">
        <div style="background:linear-gradient(135deg,#007bff 0%,#0056b3 100%);color:#fff;padding:15px;border-radius:8px 8px 0 0;box-shadow:0 4px 10px rgba(0,123,255,0.2);">
            <h3 style="margin:0;font-size:18px;">🚀 Cloudflare Mesh Engine</h3>
            <div style="font-size:11px;opacity:0.8;margin-top:4px;">Node Identity: Edge-Worker-AutoSync</div>
        </div>
        
        <div style="background:#fff;padding:15px;border-bottom:1px solid #eee;">
            <div style="color:#888;font-size:11px;font-weight:bold;margin-bottom:10px;letter-spacing:1px;">📊 SOURCE NODE ANALYSIS</div>
            {source_html}
            <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr 1fr;text-align:center;background:#fcfcfc;padding:8px;border-radius:6px;font-size:12px;border:1px solid #f0f0f0;">
                <div><div style="color:#999;font-size:10px;">RAW</div><b>{ip_stats['raw']}</b></div>
                <div><div style="color:#999;font-size:10px;">DUP</div><b style="color:#e67e22;">{ip_stats['repeat']}</b></div>
                <div><div style="color:#999;font-size:10px;">AVAIL</div><b style="color:#27ae60;">{ip_stats['final']}</b></div>
            </div>
        </div>

        <div style="background:#fff;padding:15px;">
            <div style="color:#888;font-size:11px;font-weight:bold;margin-bottom:10px;letter-spacing:1px;">🌐 CONVERGENCE STATUS</div>
            {domain_cards}
        </div>

        <div style="background:#1e1e1e;color:#d4d4d4;padding:15px;border-radius:0 0 8px 8px;font-family:monospace;font-size:11px;overflow-x:auto;line-height:1.5;">
            <div style="color:#6a9955;margin-bottom:8px;">// Runtime Execution Audit Log</div>
            {chr(10).join(audit_log)}
        </div>
    </div>
    """

    # 执行推送
    requests.post('http://www.pushplus.plus/send', json={
        "token": PUSHPLUS_TOKEN,
        "title": "Cloudflare 全自动化运维报告",
        "content": final_html,
        "template": "html"
    })

if __name__ == '__main__':
    main()
