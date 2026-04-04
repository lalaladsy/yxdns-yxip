#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare DNS 更新器 - 精准 10 IP 修复版
解决多域名下解析数量不足 10 条及推送排版混乱的问题
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
    """获取优选 IP"""
    for attempt in range(max_retries):
        try:
            # 确保请求 Top10 接口
            response = requests.get('https://ip.164746.xyz/ipTop10.html', timeout=timeout)
            if response.status_code == 200:
                return response.text
        except:
            pass
    return None

def get_dns_records(name):
    """获取 Cloudflare 后台该域名所有的 A 记录"""
    records = []
    # 增加 per_page=100 确保能一次性抓取到所有 10 条记录
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records?type=A&name={name}&per_page=100'
    try:
        response = requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            records = response.json().get('result', [])
    except:
        pass
    return records

def update_dns_record(record_info, name, cf_ip):
    """更新记录并返回结果字符串"""
    record_id = record_info['id']
    current_ip = record_info.get('content', '')

    if current_ip == cf_ip:
        return f"✅ `{cf_ip}` | {name} (最新)"

    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': cf_ip}
    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            return f"🚀 `{cf_ip}` | {name} (更新成功)"
        else:
            return f"❌ `{cf_ip}` | {name} (失败)"
    except:
        return f"⚠️ `{cf_ip}` | {name} (异常)"

def push_plus(content):
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    # 使用 Markdown 模板确保换行生效
    data = {
        "token": PUSHPLUS_TOKEN, 
        "title": "CF优选 10-IP 推送", 
        "content": content, 
        "template": "markdown"
    }
    try:
        requests.post(url, json=data, timeout=DEFAULT_TIMEOUT)
    except:
        pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("错误: 缺少必要的环境变量")
        return

    # 1. 获取并清洗 IP 列表
    ip_data = get_cf_speed_test_ip()
    if not ip_data:
        print("错误: 无法获取 IP 数据")
        return
    
    # 使用 split(',') 并通过 if ip.strip() 过滤掉末尾逗号产生的空元素
    # 这样能确保得到的 ip_list 长度正好是 10
    ip_list = [ip.strip() for ip in ip_data.split(',') if ip.strip()]
    print(f"成功获取到 {len(ip_list)} 个优选 IP")

    # 2. 拆分多个域名变量
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    
    all_push_results = []

    # 3. 循环处理域名
    for domain in target_domains:
        dns_records = get_dns_records(domain)
        record_count = len(dns_records)
        print(f"域名 {domain} 在后台共有 {record_count} 条 A 记录")

        if record_count == 0:
            all_push_results.append(f"❌ 域名 `{domain}` 在 CF 后台没有 A 记录")
            continue

        # 核心逻辑：确保更新数量与后台记录数量一致
        # 如果后台有 10 条，我们就用 ip_list 的前 10 个
        for i in range(min(len(ip_list), record_count)):
            res = update_dns_record(dns_records[i], domain, ip_list[i])
            all_push_results.append(res)

    # 4. 汇总推送，使用 \n\n 实现强制换行
    if all_push_results:
        # 每条记录占一行，显示更加美观
        formatted_message = "#### 更新详情：\n\n" + '\n\n'.join(all_push_results)
        push_plus(formatted_message)

if __name__ == '__main__':
    main()

