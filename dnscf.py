#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 核心配置 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
CF_IP_URL = os.environ.get("CF_IP_URL")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def extract_ips(text):
    pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
    return [ip for ip in re.findall(pattern, text) if all(0 <= int(p) <= 255 for p in ip.split('.'))]

def get_ips_with_audit():
    """多接口抓取并审计去重情况"""
    if not CF_IP_URL: return [], {}
    urls = [u.strip() for u in CF_IP_URL.split(',') if u.strip()]
    source_stats, all_raw_ips = {}, []
    
    for url in urls:
        name = url.split('//')[-1].split('/')[0][:15] # 提取简短域名
        try:
            res = requests.get(url, timeout=12)
            ips = extract_ips(res.text) if res.status_code == 200 else []
            source_stats[name] = len(ips)
            all_raw_ips.extend(ips)
        except Exception as e:
            source_stats[name] = f"连接失败({type(e).__name__})"
    
    unique_ips = list(dict.fromkeys(all_raw_ips)) # 保持顺序去重
    return unique_ips, {
        "sources": source_stats,
        "raw_total": len(all_raw_ips),
        "dup_count": len(all_raw_ips) - len(unique_ips),
        "final_count": len(unique_ips)
    }

def cf_api(method, url_suffix, data=None):
    """带错误追踪的 CF API 封装"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{url_suffix}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_json = res.json()
        if res.status_code == 200:
            return True, "Success", res_json
        else:
            # 提取 CF 返回的具体错误信息
            err_msg = res_json.get('errors', [{}])[0].get('message', 'Unknown Error')
            return False, f"HTTP {res.status_code}: {err_msg}", None
    except Exception as e:
        return False, f"Request Exception: {str(e)}", None

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return
    
    # 1. 抓取与审计
    unique_ips, audit = get_ips_with_audit()
    if not unique_ips: return

    # 2. 构造推送头部：接口贡献分析
    html = [f"""<div style="font-family:sans-serif;background:#f4f7f6;padding:15px;border-radius:10px;">
        <div style="background:#24292e;color:#fff;padding:12px;border-radius:8px 8px 0 0;">
            <b style="font-size:16px;">🚀 Cloudflare 自动化运维简报</b>
        </div>
        <div style="background:#fff;padding:15px;border-bottom:1px solid #eee;">
            <div style="color:#666;font-size:12px;font-weight:bold;margin-bottom:8px;">📊 数据源节点审计</div>"""]
    
    for src, count in audit['sources'].items():
        html.append(f"<div style='font-size:13px;margin-bottom:3px;'>• <code>{src}</code> : <b>{count}</b> IP</div>")
    
    html.append(f"""<div style="margin-top:10px;background:#f9fafb;padding:10px;border-radius:6px;font-size:12px;border:1px solid #eaecef;display:flex;justify-content:space-around;text-align:center;">
        <div><div style="color:#999;font-size:10px;">RAW</div><b>{audit['raw_total']}</b></div>
        <div><div style="color:#999;font-size:10px;">DUP</div><b style="color:#e67e22;">{audit['dup_count']}</b></div>
        <div><div style="color:#999;font-size:10px;">FINAL</div><b style="color:#27ae60;">{audit['final_count']}</b></div>
    </div></div>""")

    # 3. 域名解析处理
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    for domain in target_domains:
        # 获取现有记录
        success, msg, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success:
            html.append(f"<div style='background:#fff;padding:15px;color:#d73a49;'>❌ {domain} 获取失败: {msg}</div>")
            continue

        old_records = sorted(res.get('result', []), key=lambda x: x['id'])
        old_count, new_count = len(old_records), len(unique_ips)
        ops = {"upd": 0, "add": 0, "del": 0, "err": 0}
        error_logs = []

        for i in range(max(new_count, old_count)):
            if i < new_count and i < old_count:
                if old_records[i]['content'] != unique_ips[i]:
                    ok, emsg, _ = cf_api("PUT", f"dns_records/{old_records[i]['id']}", {"type":"A","name":domain,"content":unique_ips[i],"ttl":60})
                    if ok: ops["upd"] += 1
                    else: ops["err"] += 1; error_logs.append(emsg)
            elif i < new_count:
                ok, emsg, _ = cf_api("POST", "dns_records", {"type":"A","name":domain,"content":unique_ips[i],"ttl":60})
                if ok: ops["add"] += 1
                else: ops["err"] += 1; error_logs.append(emsg)
            elif i < old_count:
                ok, emsg, _ = cf_api("DELETE", f"dns_records/{old_records[i]['id']}")
                if ok: ops["del"] += 1
                else: ops["err"] += 1; error_logs.append(emsg)
            time.sleep(0.2)

        # 4. 构造域名卡片
        err_html = f"<div style='color:#d73a49;font-size:11px;margin-top:5px;'>⚠ 失败原因: {list(set(error_logs))[0] if error_logs else ''}</div>" if ops['err'] > 0 else ""
        html.append(f"""
        <div style="background:#fff;padding:15px;border-bottom:1px solid #eee;">
            <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
                <b style="color:#0366d6;font-size:14px;">🌐 {domain}</b>
                <span style="background:#28a745;color:#fff;font-size:10px;padding:1px 6px;border-radius:4px;">DONE</span>
            </div>
            <div style="font-size:12px;color:#586069;line-height:1.6;">
                弹性收缩: <code>{old_count}</code> ➔ <b style="color:#007bff;">{new_count}</b><br>
                执行统计: 更新({ops['upd']}) | 新增({ops['add']}) | 删除({ops['del']}) | 失败(<span style="color:#d73a49;">{ops['err']}</span>)<br>
                最终成功解析: <b>{new_count - ops['err']}</b> 个 IP
                {err_html}
            </div>
        </div>""")

    html.append(f"<div style='text-align:center;font-size:10px;color:#999;padding:10px;'>Sync Time: {time.strftime('%Y-%m-%d %H:%M:%S')}</div></div>")

    # 5. 发送
    requests.post('http://www.pushplus.plus/send', json={
        "token": PUSHPLUS_TOKEN,
        "title": "Cloudflare 自动化运维简报",
        "content": "".join(html),
        "template": "html"
    })

if __name__ == '__main__':
    main()
