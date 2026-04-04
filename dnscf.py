#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 核心配置 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
# 重点：这里的 URL 可以填一个，也可以填多个（用英文逗号隔开）
CF_IP_URL = os.environ.get("CF_IP_URL") 
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def extract_ips(text):
    """提取 IPv4 逻辑"""
    pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    found = re.findall(pattern, text)
    return [ip for ip in found if all(0 <= int(p) <= 255 for p in ip.split('.'))]

def get_ips_safe():
    """多接口容错抓取"""
    if not CF_IP_URL: return [], {}
    # 自动处理空格和中英文逗号
    raw_urls = CF_IP_URL.replace('，', ',').split(',')
    urls = [u.strip() for u in raw_urls if u.strip()]
    
    source_stats, all_raw_ips = {}, []
    
    for url in urls:
        # 提取域名作为显示名称
        display_name = url.split('//')[-1].split('/')[0][:15]
        try:
            # 增加超时控制，防止一个接口卡死全家
            res = requests.get(url, timeout=10) 
            if res.status_code == 200:
                ips = extract_ips(res.text)
                source_stats[display_name] = len(ips)
                all_raw_ips.extend(ips)
            else:
                source_stats[display_name] = f"失败({res.status_code})"
        except:
            source_stats[display_name] = "超时/连接失败"
            
    # 智能去重
    unique_ips = list(dict.fromkeys(all_raw_ips))
    stats = {
        "sources": source_stats,
        "raw": len(all_raw_ips),
        "dup": len(all_raw_ips) - len(unique_ips),
        "final": len(unique_ips)
    }
    return unique_ips, stats

def cf_request(method, endpoint, data=None):
    """封装 CF API，返回 (是否成功, 错误信息)"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        if res.status_code == 200 and res_j.get('success'):
            return True, "OK"
        else:
            msg = res_j.get('errors', [{}])[0].get('message', '未知错误')
            return False, f"CF报错: {msg}"
    except Exception as e:
        return False, str(e)

def main():
    # 校验基础变量
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("缺少必要变量: API_TOKEN 或 ZONE_ID 或 DNS_NAME")
        return

    # 1. 抓取数据 (增加保护，如果没抓到不直接退出，而是尝试分析原因)
    unique_ips, audit = get_ips_safe()
    if not unique_ips:
        print("未获取到任何 IP，检查 CF_IP_URL 是否配置正确")
        return

    # 2. 准备推送 HTML
    html = [f"""<div style="font-family:sans-serif;padding:15px;background:#f8f9fa;border-radius:10px;border:1px solid #ddd;">
        <div style="background:#007bff;color:#fff;padding:10px;border-radius:5px;margin-bottom:15px;">
            <b style="font-size:16px;">🌐 Cloudflare 运维简报</b>
        </div>
        <div style="padding:10px;background:#fff;border-radius:5px;margin-bottom:10px;border-left:4px solid #17a2b8;">
            <b style="font-size:13px;color:#666;">📊 接口审计</b>"""]
    
    for src, count in audit['sources'].items():
        html.append(f"<div style='font-size:12px;margin:3px 0;'>• {src} : <b>{count}</b></div>")
    
    html.append(f"""<div style="margin-top:8px;font-size:12px;color:#999;border-top:1px dashed #eee;padding-top:5px;">
        总计: {audit['raw']} | 重复: {audit['dup']} | <b>最终可用: {audit['final']}</b>
    </div></div>""")

    # 3. 处理域名
    target_domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    for domain in target_domains:
        # 获取旧记录
        get_ok, get_msg = cf_request("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not get_ok:
            html.append(f"<div style='color:red;'>❌ {domain} 获取失败: {get_msg}</div>")
            continue
        
        # 重新获取结果，因为上面封装只返回了布尔
        curr_res = requests.get(f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records?type=A&name={domain}&per_page=100', headers=HEADERS).json()
        old_records = sorted(curr_res.get('result', []), key=lambda x: x['id'])
        
        oc, nc = len(old_records), len(unique_ips)
        ops = {"u": 0, "a": 0, "d": 0, "e": 0}
        last_err = ""

        for i in range(max(nc, oc)):
            if i < nc and i < oc:
                if old_records[i]['content'] != unique_ips[i]:
                    ok, msg = cf_request("PUT", f"dns_records/{old_records[i]['id']}", {"type":"A","name":domain,"content":unique_ips[i],"ttl":60})
                    if ok: ops["u"]+=1
                    else: ops["e"]+=1; last_err = msg
                else: ops["u"]+=0 # 保持
            elif i < nc:
                ok, msg = cf_request("POST", "dns_records", {"type":"A","name":domain,"content":unique_ips[i],"ttl":60})
                if ok: ops["a"]+=1
                else: ops["e"]+=1; last_err = msg
            elif i < oc:
                ok, msg = cf_request("DELETE", f"dns_records/{old_records[i]['id']}")
                if ok: ops["d"]+=1
                else: ops["e"]+=1; last_err = msg
            time.sleep(0.2)

        # 4. 组装域名卡片
        err_info = f"<div style='color:red;font-size:11px;'>最后报错: {last_err}</div>" if ops['e'] > 0 else ""
        html.append(f"""<div style="padding:10px;background:#fff;border-radius:5px;border-left:4px solid #28a745;margin-bottom:10px;">
            <b style="font-size:14px;color:#333;">{domain}</b>
            <div style="font-size:12px;color:#666;margin-top:5px;">
                收缩状态: {oc} ➔ <b>{nc}</b><br>
                执行记录: 更新({ops['u']}) | 新增({ops['a']}) | 删除({ops['d']}) | 失败({ops['e']})
                {err_info}
            </div>
        </div>""")

    html.append(f"<div style='text-align:center;color:#ccc;font-size:10px;'>Sync at {time.strftime('%H:%M:%S')}</div></div>")

    # 5. 发送推送
    requests.post('http://www.pushplus.plus/send', json={
        "token": PUSHPLUS_TOKEN,
        "title": "CF DNS 自动化运维报告",
        "content": "".join(html),
        "template": "html"
    })

if __name__ == '__main__':
    main()
