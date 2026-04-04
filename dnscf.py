#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests, re

# --- 权限配置 (仅保留敏感密钥在 Secrets) ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

# --- 硬编码接口列表 ---
SOURCE_URLS = [
    "https://vps789.com/openApi/cfIpApi",
    "https://cf.001315.xyz/cu",
    "https://cf.001315.xyz/cmcc",
    "https://cf.001315.xyz/ct",
    "https://ip.164746.xyz/ipTop10.html"
]

HEADERS = {'Authorization': f'Bearer {CF_API_TOKEN}', 'Content-Type': 'application/json'}

def get_ips_audit():
    """硬核抓取：多接口正则提取 + 智能去重"""
    source_stats, all_raw_ips = {}, []
    
    for url in SOURCE_URLS:
        name = url.split('/')[-1] if len(url.split('/')[-1]) > 2 else url.split('/')[-2]
        try:
            # 增加随机 Header 模拟浏览器，防止被某些接口拦截
            res = requests.get(url, timeout=12, headers={'User-Agent': 'Mozilla/5.0'})
            if res.status_code == 200:
                # 强力正则：只抓 IPv4，过滤掉 HTML 标签和杂质
                ips = [ip for ip in re.findall(r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b', res.text) 
                       if all(0 <= int(p) <= 255 for p in ip.split('.'))]
                source_stats[name] = len(ips)
                all_raw_ips.extend(ips)
            else:
                source_stats[name] = f"Error:{res.status_code}"
        except:
            source_stats[name] = "Timeout/Fail"

    # 顺序去重
    unique_ips = list(dict.fromkeys(all_raw_ips))
    return unique_ips, {
        "sources": source_stats,
        "raw": len(all_raw_ips),
        "dup": len(all_raw_ips) - len(unique_ips),
        "final": len(unique_ips)
    }

def cf_api(method, endpoint, data=None):
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/{endpoint}'
    try:
        res = requests.request(method, url, headers=HEADERS, json=data, timeout=10)
        res_j = res.json()
        if res.status_code == 200 and res_j.get('success'):
            return True, "OK", res_j
        else:
            msg = res_j.get('errors', [{}])[0].get('message', 'Cloudflare API 异常')
            return False, msg, None
    except Exception as e:
        return False, str(e), None

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("❌ 缺少关键配置，请检查 GitHub Secrets")
        return

    # 1. 审计与去重
    ips, audit = get_ips_audit()
    if not ips:
        print("⚠️ 未抓取到任何 IP，脚本终止")
        return

    # 2. 构造推送报告
    report = [f"### 📊 节点数据审计 (Total: {audit['final']})"]
    for src, count in audit['sources'].items():
        report.append(f"- `Source: {src}` → **{count}** IP")
    report.append(f"\n> 原始总计: {audit['raw']} | 重复过滤: {audit['dup']} | **最终生效: {audit['final']}**\n\n---")

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

        # 核心伸缩算法
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

        # 4. 组装域名审计结果
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
            "title": "CF 优选自动伸缩报告",
            "content": "\n".join(report),
            "template": "markdown"
        })

if __name__ == '__main__':
    main()
