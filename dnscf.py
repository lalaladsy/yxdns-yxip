#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, time, os, requests

# API 配置
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {
    'Authorization': f'Bearer {CF_API_TOKEN}',
    'Content-Type': 'application/json'
}

def get_cf_speed_test_ip():
    """精准获取 10 个 IP，排除所有干扰项"""
    try:
        response = requests.get('https://ip.164746.xyz/ipTop10.html', timeout=10)
        if response.status_code == 200:
            # 先统一替换换行符，再按逗号拆分，最后过滤掉空格和空值
            raw = response.text.replace('\n', ',').split(',')
            ips = [ip.strip() for ip in raw if ip.strip()]
            # 此时 ips 列表长度应为 10
            return ips[:10]
    except:
        return []

def get_dns_records(name):
    """获取该域名的所有 A 记录（强制抓取 50 条以内，防止分页漏掉）"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    params = {'type': 'A', 'name': name, 'per_page': 50}
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
    except:
        return []

def update_dns_record(record_info, name, cf_ip):
    """更新逻辑：如果 IP 一样就跳过，不一样就 PUT"""
    record_id = record_info['id']
    current_ip = record_info.get('content', '')

    if current_ip == cf_ip:
        return f"✅ `{cf_ip}` | {name} (最新)"

    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': cf_ip}
    try:
        # 失败自动重试 1 次，增加稳定性
        for _ in range(2):
            response = requests.put(url, headers=HEADERS, json=data, timeout=10)
            if response.status_code == 200:
                return f"🚀 `{cf_ip}` | {name} (成功)"
            time.sleep(0.5)
        return f"❌ `{cf_ip}` | {name} (失败:{response.status_code})"
    except:
        return f"⚠️ `{cf_ip}` | {name} (异常)"

def push_plus(content):
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN, 
        "title": "CF优选 10-IP 报告", 
        "content": content, 
        "template": "markdown"
    }
    try: requests.post(url, json=data, timeout=10)
    except: pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return

    # 1. 拿到 10 个优选 IP
    ip_list = get_cf_speed_test_ip()
    if not ip_list: return

    # 2. 拿到所有需要更新的域名
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    all_results = []

    for domain in target_domains:
        # 3. 拿到该域名在 CF 的坑位
        dns_records = get_dns_records(domain)
        
        # 调试输出：可以在 Actions 日志中看到真实的坑位数量
        print(f"--- 正在处理 {domain}，识别到 {len(dns_records)} 条 A 记录 ---")

        # 4. 核心对齐：zip 会以最短的列表为准。
        # 只要 dns_records 是 10 条，ip_list 是 10 条，它就一定会跑 10 次。
        for record, ip in zip(dns_records, ip_list):
            res = update_dns_record(record, domain, ip)
            all_results.append(res)
            time.sleep(0.5) # 稍微停顿，对 API 友好一点

    # 5. 汇总并按照你的要求“一行一个”推送
    if all_results:
        # 使用 \n\n 强制换行
        final_msg = "#### 更新详情 (每域名 10 条)：\n\n" + '\n\n'.join(all_results)
        push_plus(final_msg)

if __name__ == '__main__':
    main()
