#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re, subprocess, platform
from concurrent.futures import ThreadPoolExecutor

# --- 核心凭据 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

# --- 优选策略配置 ---
MAX_FINAL_IPS = 15     # 最终入库 IP 数量
PING_THREADS = 25      # 并发测速线程
SOURCE_URLS = [
    "https://vps789.com/openApi/cfIpApi",
    "https://cf.001315.xyz/cu",
    "https://cf.001315.xyz/cmcc",
    "https://cf.001315.xyz/ct",
    "https://ip.164746.xyz/ipTop10.html"
]

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ping_latency(ip):
    """单路 Ping 探测引擎"""
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
    for url in SOURCE_URLS:
        name = url.split('/')[-1] if len(url.split('/')[-1]) > 5 else url.split('/')[-2]
        try:
            res = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
            if res.status_code == 200:
                ips = [ip for ip in re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text) 
                       if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
        except: source_stats[name] = "ERR"

    unique_ips = list(dict.fromkeys(all_raw_ips))
    with ThreadPoolExecutor(max_workers=PING_THREADS) as executor:
        results = list(executor.map(get_ping_latency, unique_ips))
    
    valid_results = sorted([r for r in results if r[1] < 9999], key=lambda x: x[1])
    final_results = valid_results[:MAX_FINAL_IPS]
    
    latency_range = f"{final_results[0][1]}ms ~ {final_results[-1][1]}ms" if final_results else "N/A"
    
    return [x[0] for x in final_results], {
        "sources": source_stats,
        "raw": len(all_raw_ips),
        "unique": len(unique_ips),
        "valid": len(valid_results),
        "latency": latency_range
    }

def cf_api(method, endpoint, data=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        return (True, "OK", res_j) if res.status_code == 200 and res_j.get('success') else (False, "API_ERR", res_j)
    except Exception as e:
        return False, str(e), None

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return
    ips, audit = get_ips_and_rank()
    if not ips: return

    # --- 专业版报告构建 ---
    report = [
        "## 🛠️ Cloudflare 优选同步审计报告",
        f"> **执行时间**: `{time.strftime('%Y-%m-%d %H:%M:%S')}`",
        "\n### 📊 节点优选质量审计",
        f"| 指标 | 统计数值 |",
        f"| :--- | :--- |",
        f"| **优选范围 (Latency)** | `{audit['latency']}` |",
        f"| **海选/去重/通路** | `{audit['raw']}` / `{audit['unique']}` / `{audit['valid']}` |",
        f"| **最终入库策略** | `Top {len(ips)} (升序)` |",
        "\n#### 📡 接口贡献负载"
    ]
    
    # 接口贡献采用行内代码块展示
    report.append("` " + " | ".join([f"{k}: {v}" for k, v in audit['sources'].items()]) + " `")
    report.append("\n---\n### 🌐 域名同步状态看板")

    domains = [d.strip() for d in CF_DNS_NAME.replace('，', ',').split(',') if d.strip()]
    for domain in domains:
        success, _, res = cf_api("GET", f"dns_records?type=A&name={domain}&per_page=100")
        if not success:
            report.append(f"❌ **{domain}**: `数据获取指令失效`")
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

        # 域名详情展示
        report.append(f"#### 🔹 {domain}")
        report.append(f"- **规模演变**: `Count {oc}` ➔ `Count {nc}`")
        report.append(f"- **操作记录**: `更新:{ops['u']}` | `新增:{ops['a']}` | `删除:{ops['d']}`")
        if ops['e'] > 0:
            report.append(f"- ⚠️ <font color=\"#dd0000\">检测到 {ops['e']} 项异常，请检查 Cloudflare 审计日志</font>")

    report.append("\n---\n> **Status**: `Sync Completed Successfully`")

    if PUSHPLUS_TOKEN:
        requests.post('http://www.pushplus.plus/send', json={
            "token": PUSHPLUS_TOKEN,
            "title": "CF DNS 自动化同步报告",
            "content": "\n".join(report),
            "template": "markdown"
        })

if __name__ == '__main__':
    main()
