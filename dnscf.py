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
    """获取并精准提取 10 个有效 IP"""
    try:
        response = requests.get('https://ip.164746.xyz/ipTop10.html', timeout=10)
        if response.status_code == 200:
            # 兼容逗号和换行，彻底过滤空值
            raw_ips = response.text.replace('\n', ',').split(',')
            ips = [ip.strip() for ip in raw_ips if ip.strip()]
            # 强制返回前 10 个（如果够的话）
            return ips[:10]
    except:
        return []

def get_dns_records(name):
    """获取该域名的所有 A 记录，确保不被分页截断"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    # 增加 per_page 确保一次性抓完
    params = {'type': 'A', 'name': name, 'per_page': 50}
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
    except:
        return []

def update_dns_record(record_info, name, cf_ip):
    """执行更新，并返回带 Emoji 的整齐结果"""
    record_id = record_info['id']
    current_ip = record_info.get('content', '')

    if current_ip == cf_ip:
        return f"✅ `{cf_ip}` | {name} (最新)"

    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': cf_ip}
    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=10)
        if response.status_code == 200:
            return f"🚀 `{cf_ip}` | {name} (成功)"
        else:
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

    # 1. 拿到 10 个 IP
    ip_list = get_cf_speed_test_ip()
    if not ip_list: return

    # 2. 拿到域名列表
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    all_results = []

    for domain in target_domains:
        # 3. 拿到该域名在 CF 的所有坑位
        dns_records = get_dns_records(domain)
        
        # 调试信息：会在 Actions 日志里显示
        print(f"域名 {domain} 识别到 {len(dns_records)} 条记录，准备匹配 {len(ip_list)} 个 IP")

        # 4. 使用 zip 严格对齐 IP 和 记录
        # 只要 dns_records 是 10 个，ip_list 是 10 个，这里就会精准跑 10 次
        for record, ip in zip(dns_records, ip_list):
            res = update_dns_record(record, domain, ip)
            all_results.append(res)
            # 稍微停顿，避免请求过快
            time.sleep(0.5)

    # 5. 汇总推送，强制换行
    if all_results:
        # 每一个条目占一行
        final_msg = "#### 更新详情 (每域名 10 条)：\n\n" + '\n\n'.join(all_results)
        push_plus(final_msg)

if __name__ == '__main__':
    main()
