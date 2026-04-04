#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests

# --- 环境变量获取 ---
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")  # 域名1,域名2
CF_IP_URL = os.environ.get("CF_IP_URL")      # 接口1,接口2
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {
    'Authorization': f'Bearer {CF_API_TOKEN}',
    'Content-Type': 'application/json'
}

def get_all_ips():
    """从多个接口获取 IP 并去重"""
    if not CF_IP_URL:
        return []
    
    urls = [u.strip() for u in CF_IP_URL.split(',') if u.strip()]
    combined_ips = []
    
    for url in urls:
        try:
            print(f"正在从接口获取 IP: {url}")
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                # 兼容换行和逗号，提取所有非空字符串
                raw_data = response.text.replace('\n', ',').split(',')
                ips = [ip.strip() for ip in raw_data if ip.strip()]
                combined_ips.extend(ips)
        except Exception as e:
            print(f"接口请求失败 {url}: {e}")
            
    # 去重并保持顺序（如果有重复 IP，只保留第一个出现的）
    seen = set()
    unique_ips = [x for x in combined_ips if not (x in seen or seen.add(x))]
    return unique_ips

def get_dns_records(name):
    """获取 CF 现有的 A 记录"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    params = {'type': 'A', 'name': name, 'per_page': 100}
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
    except:
        return []

def modify_dns(method, name, ip=None, record_id=None):
    """封装 增/删/改 操作"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    if record_id:
        url += f'/{record_id}'
    
    data = {'type': 'A', 'name': name, 'content': ip, 'ttl': 60, 'proxied': False}
    
    try:
        if method == "POST":
            res = requests.post(url, headers=HEADERS, json=data, timeout=10)
        elif method == "PUT":
            res = requests.put(url, headers=HEADERS, json=data, timeout=10)
        elif method == "DELETE":
            res = requests.delete(url, headers=HEADERS, timeout=10)
        return res.status_code == 200
    except:
        return False

def push_plus(content):
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": "CF DNS 自动伸缩同步报告",
        "content": content,
        "template": "markdown"
    }
    try:
        requests.post(url, json=data, timeout=10)
    except:
        pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME, CF_IP_URL]):
        print("错误: 环境变量配置不完整")
        return

    # 1. 汇总所有接口的 IP
    all_new_ips = get_all_ips()
    total_new_count = len(all_new_ips)
    if total_new_count == 0:
        print("未能从任何接口获取到 IP")
        return

    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    full_report = [f"### 🚀 全自动同步完成\n\n**总计获取去重 IP 数**: `{total_new_count}`\n\n---\n"]

    for domain in target_domains:
        old_records = get_dns_records(domain)
        old_count = len(old_records)
        
        stats = {"update": 0, "create": 0, "delete": 0, "skip": 0}
        
        # 确定循环次数（以 IP 数和坑位数中较大的为准）
        max_loop = max(total_new_count, old_count)
        
        for i in range(max_loop):
            # 情况 1：新 IP 还有，老坑位也有 -> 执行更新
            if i < total_new_count and i < old_count:
                if old_records[i]['content'] != all_new_ips[i]:
                    if modify_dns("PUT", domain, all_new_ips[i], old_records[i]['id']):
                        stats["update"] += 1
                else:
                    stats["skip"] += 1
            
            # 情况 2：新 IP 还有，老坑位用完了 -> 执行新增
            elif i < total_new_count:
                if modify_dns("POST", domain, all_new_ips[i]):
                    stats["create"] += 1
            
            # 情况 3：老坑位还有，新 IP 用完了 -> 执行删除多余坑位
            elif i < old_count:
                if modify_dns("DELETE", domain, record_id=old_records[i]['id']):
                    stats["delete"] += 1
            
            time.sleep(0.3) # 稍微防一下频率限制

        # 组装通知内容
        domain_report = f"#### 🌐 {domain}\n"
        domain_report += f"- 坑位变动: `{old_count}` → `{total_new_count}`\n"
        domain_report += f"- 详细操作: 更新`{stats['update']}` | 新增`{stats['create']}` | 删除`{stats['delete']}` | 保持`{stats['skip']}`\n\n"
        full_report.append(domain_report)

    # 4. 推送
    push_plus("".join(full_report))

if __name__ == '__main__':
    main()
