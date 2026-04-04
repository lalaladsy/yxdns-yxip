#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cloudflare DNS 更新器
支持多域名循环更新 & 汇总推送
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

# 请求头
HEADERS = {
    'Authorization': f'Bearer {CF_API_TOKEN}',
    'Content-Type': 'application/json'
}

# 默认超时时间（秒）
DEFAULT_TIMEOUT = 30


def get_cf_speed_test_ip(timeout=10, max_retries=5):
    """获取 Cloudflare 优选 IP"""
    for attempt in range(max_retries):
        try:
            # 这里已经按照你的要求改成了 ipTop10.html
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
    """获取指定名称的 DNS 记录列表"""
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
        current_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        print(f"跳过: {name} 的 IP {cf_ip} 已是最新")
        return f"ip:{cf_ip} 解析 {name} 跳过 (已是最新)"

    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': cf_ip}

    try:
        response = requests.put(url, headers=HEADERS, json=data, timeout=DEFAULT_TIMEOUT)
        if response.status_code == 200:
            return f"ip:{cf_ip} 解析 {name} 成功"
        else:
            return f"ip:{cf_ip} 解析 {name} 失败: {response.text}"
    except Exception as e:
        return f"ip:{cf_ip} 解析 {name} 异常: {e}"


def push_plus(content):
    """发送 PushPlus 消息推送"""
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": "IP优选DNSCF推送",
        "content": content,
        "template": "markdown"
    }
    try:
        requests.post(url, json=data, timeout=DEFAULT_TIMEOUT)
    except:
        pass


def main():
    """主函数"""
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]):
        print("错误: 缺少必要的环境变量")
        return

    # 1. 获取最新优选 IP
    ip_addresses_str = get_cf_speed_test_ip()
    if not ip_addresses_str: return

    # 2. 保留原逻辑：按逗号切分 IP
    ip_addresses = [ip.strip() for ip in ip_addresses_str.split(',') if ip.strip()]
    
    # 3. 核心改动：支持变量填入多个域名，循环处理
    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    
    all_push_results = []

    for domain in target_domains:
        print(f"正在处理域名: {domain}")
        dns_records = get_dns_records(domain)
        
        if not dns_records:
            print(f"错误: 未找到 {domain} 的记录")
            continue

        # 匹配 IP 数量和该域名的 A 记录数量
        current_ips = ip_addresses[:len(dns_records)]

        for index, ip_address in enumerate(current_ips):
            result = update_dns_record(dns_records[index], domain, ip_address)
            all_push_results.append(result)

    # 4. 只推送一次：汇总所有域名的结果
    if all_push_results:
        push_plus('\n'.join(all_push_results))


if __name__ == '__main__':
    main()
