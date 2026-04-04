#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare DNS 更新器
支持多域名、Top10 IP，并提供清晰的换行推送
"""

import json
import traceback
import time
import os
import requests

# API 配置
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
CF_DNS_NAME = os.environ.get("CF_DNS_NAME")
PUSHPLUS_TOKEN = os.environ.get("PUSHPLUS_TOKEN")

HEADERS = {
    'Authorization': f'Bearer {CF_API_TOKEN}',
    'Content-Type': 'application/json'
}

DEFAULT_TIMEOUT = 30

def get_cf_speed_test_ip(timeout=10, max_retries=5):
    """获取 Cloudflare 优选 IP (Top10)"""
    for attempt in range(max_retries):
        try:
            response = requests.get(
                'https://ip.164746.xyz/ipTop10.html',
                timeout=timeout
            )
            if response.status_code == 200:
                return response.text
        except Exception as e:
            print(f"获取优选 IP 失败 (尝试 {attempt + 1}/{max_retries}): {e}")
    return None

def get_dns_records(name):
    """获取指定域名的 A 记录列表"""
    records = []
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    try:
        response = requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            result = response.json().get('result', [])
            for record in result:
                if record.get('name') == name and record.get('type') == 'A':
                    records.append({
                        'id': record['id'],
                        'content': record.get('content', '')
                    })
    except Exception as e:
        print(f'获取 DNS 记录异常: {e}')
    return records

def update_dns_record(record_info, name, cf_ip):
    """更新单条 DNS 记录"""
    record_id = record_info['id']
    current_ip = record_info.get('content', '')

    if current_ip == cf_ip:
        return f"✅ **{name}** | `{cf_ip}` (最新)"

    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': cf_ip}
    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            return f"🚀 **{name}** | `{cf_ip}` (更新成功)"
        else:
            return f"❌ **{name}** | `{cf_ip}` (失败: {response.status_code})"
    except:
        return f"⚠️ **{name}** | `{cf_ip}` (连接异常)"

def push_plus(content):
    """发送 PushPlus 消息推送"""
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": "Cloudflare优选推送",
        "content": content,
        "template": "markdown" # 必须使用 markdown 模板
    }
    try:
        requests.post(url, json=data, timeout=DEFAULT_TIMEOUT)
    except:
        pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("错误: 缺少必要的环境变量")
        return

    # 1. 获取优选 IP
    ip_data = get_cf_speed_test_ip()
    if not ip_data: return
    # 按照你的测试，使用逗号分割
    ip_list = [ip.strip() for ip in ip_data.split(',') if ip.strip()]

    # 2. 域名列表
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    
    all_results = []
    
    # 3. 循环处理
    for domain in target_domains:
        dns_records = get_dns_records(domain)
        if not dns_records: continue

        ips_to_use = ip_list[:len(dns_records)]
        
        for index, ip in enumerate(ips_to_use):
            res = update_dns_record(dns_records[index], domain, ip)
            all_results.append(res)

    # 4. 汇总推送（核心：使用双换行确保一行一个）
    if all_results:
        # 每条结果后面加两个换行符，PushPlus 的 Markdown 才会显示为新行
        formatted_content = '\n\n'.join(all_results)
        push_plus(formatted_content)

if __name__ == '__main__':
    main()
