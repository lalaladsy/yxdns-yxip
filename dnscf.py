#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re, subprocess, platform
from concurrent.futures import ThreadPoolExecutor

# --- 权限配置 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

# --- 优选策略配置 ---
MAX_FINAL_IPS = 15     # 最终解析的最优 IP 数量
PING_THREADS = 30      # 测速并发线程数
SOURCE_URLS = [
    "https://vps789.com/openApi/cfIpApi",
    "https://cf.001315.xyz/cu",
    "https://cf.001315.xyz/cmcc",
    "https://cf.001315.xyz/ct",
    "https://ip.164746.xyz/ipTop10.html"
]

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ping_latency(ip):
    """底层测速引擎"""
    param = '-n' if platform.system().lower() == 'windows' else '-c'
    cmd = ['ping', param, '1', '-W', '1', ip]
    try:
        start = time.time()
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return ip, int((time.time() - start) * 1000)
    except:
        return ip, 9999

def get_ips_and_rank():
    source_stats, all_raw_ips = {}, []
    
    # 1. 抓取
    for url in SOURCE_URLS:
        name = url.split('/')[-1] if len(url.split('/')[-1]) > 5 else url.split('/')[-2]
        try:
            res = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
            if res.status_code == 200:
                found = re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text)
                ips = [ip for ip in found if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
        except: source_stats[name] = "获取失败"

    # 2. 去重
    unique_ips = list(dict.fromkeys(all_raw_ips))
    
    # 3. 高并发测速
    print(f"📡 启动测速：针对 {len(unique_ips)} 个独立 IP...")
    with ThreadPoolExecutor(max_workers=PING_THREADS) as executor:
        results = list(executor.map(get_ping_latency, unique_ips))
    
    # 4. 筛选最优
    valid_results = sorted([r for r in results if r[1] < 9999], key=lambda x: x[1])
    final_results = valid_results[:MAX_FINAL_IPS]
    
    # 5. 格式化最优 IP 列表用于通知
    top_ips_display = "、".join([f"`{r[0]}({r[1]}ms)`" for r in final_results[:5]])
    
    return [x[0] for x in final_results], {
        "sources": source_stats,
        "raw": len(all_raw_ips),
        "dup": len(all_raw_ips) - len(unique_ips),
        "valid": len(valid_results),
        "top_ips": top_ips_display
    }

def cf_api(method, endpoint, data=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        if res.status_code == 200 and res_j.get('success'):
            return True, "OK", res_j
        else:
            msg = res_j.get('errors', [{}])[0].get('message', 'CF接口异常')
            return False, msg, None
    except: return False, "网络异常", None

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return

    # 1. 测速与优选
    ips, audit = get_ips_and_rank()
    if not ips: return

    # 2. 构造推送报告 (回归你要求的列表格式)
    report = [f"### 📊 节点优选审计报告 (入选: {len(ips)})"]
    for src, count in audit['sources'].items():
        report.append(f"- `Source: {src}` → **{count}** IP")
    
    report.append(f"\n> **优选统计**: 原始 `{audit['raw']}` | 重复过滤 `{audit['dup']}` | 有效通路 `{audit['valid']}`")
    report.append(f"> **核心优选**: {audit['top_ips']} 等")
    report.append("\n---")

    # 3. 域名弹性同步
    domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    for domain in domains:
        success, msg, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success:
            report.append(f"❌ **{domain}**: 获取失败 ({msg})")
            continue
            
        old_recs = sorted(res.get('result', []), key=lambda x: x['id'])
        oc, nc = len(old_recs), len(ips)
        ops = {"u": 0, "a": 0, "d": 0, "e": 0}
        err_log = ""

        for i in range(max(nc, oc)):
            if i < nc and i < oc:
                if old_recs[i]['content'] != ips[i]:
                    ok, emsg, _ = cf_api("PUT", f"dns_records/{old_recs[i]['id']}", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                    if ok: ops["u"]+=1
                    else: ops["e"]+=1; err_log = emsg
            elif i < nc:
                ok, emsg, _ = cf_api("POST", "dns_records", {"type":"A","name":domain,"content":ips[i],"ttl":60})
                if ok: ops["a"]+=1
                else: ops["e"]+=1; err_log = emsg
            elif i < oc:
                ok, emsg, _ = cf_api("DELETE", f"dns_records/{old_recs[i]['id']}")
                if ok: ops["d"]+=1
                else: ops["e"]+=1; err_log = emsg
            time.sleep(0.3)

        # 4. 域名审计结果
        status = f"### 🌐 域名: {domain}\n"
        status += f"- **弹性规模**: `{oc}` ➔ `{nc}`\n"
        status += f"- **执行详情**: 更新 `{ops['u']}` | 新增 `{ops['a']}` | 删除 `{ops['d']}`"
        if ops['e'] > 0:
            status += f" | <span style='color:red;'>失败 `{ops['e']}` ({err_log})</span>"
        report.append(status + "\n")

    # 5. 推送
    if PUSHPLUS_TOKEN:
        requests.post('http://www.pushplus.plus/send', json={
            "token": PUSHPLUS_TOKEN,
            "title": "CF 优选精选同步报告",
            "content": "\n".join(report),
            "template": "markdown"
        })

if __name__ == '__main__':
    main()
