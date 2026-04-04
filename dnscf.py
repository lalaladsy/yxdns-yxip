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
    """获取优选 IP，返回实际获取到的所有 IP 列表"""
    try:
        response = requests.get('https://ip.164746.xyz/ipTop10.html', timeout=10)
        if response.status_code == 200:
            # 彻底清洗数据
            raw = response.text.replace('\n', ',').split(',')
            ips = [ip.strip() for ip in raw if ip.strip()]
            return ips
    except:
        return []

def get_dns_records(name):
    """获取该域名的所有 A 记录"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    params = {'type': 'A', 'name': name, 'per_page': 100}
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
    except:
        return []

def create_dns_record(name, ip):
    """新增一条 A 记录"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records'
    data = {'type': 'A', 'name': name, 'content': ip, 'ttl': 60, 'proxied': False}
    try:
        res = requests.post(url, headers=HEADERS, json=data, timeout=10)
        return res.status_code == 200
    except:
        return False

def delete_dns_record(record_id):
    """删除一条记录"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    try:
        res = requests.delete(url, headers=HEADERS, timeout=10)
        return res.status_code == 200
    except:
        return False

def update_dns_record(record_id, name, ip):
    """更新已有记录"""
    url = f'https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{record_id}'
    data = {'type': 'A', 'name': name, 'content': ip, 'ttl': 60, 'proxied': False}
    try:
        res = requests.put(url, headers=HEADERS, json=data, timeout=10)
        return res.status_code == 200
    except:
        return False

def push_plus(title, content):
    if not PUSHPLUS_TOKEN: return
    url = 'http://www.pushplus.plus/send'
    data = {"token": PUSHPLUS_TOKEN, "title": title, "content": content, "template": "markdown"}
    try: requests.post(url, json=data, timeout=10)
    except: pass

def main():
    if not all([CF_API_TOKEN, CF_ZONE_ID, CF_DNS_NAME]): return

    # 1. 获取优选 IP
    new_ips = get_cf_speed_test_ip()
    ip_count = len(new_ips)
    if ip_count == 0: return

    target_domains = [d.strip() for d in CF_DNS_NAME.split(',') if d.strip()]
    report = []

    for domain in target_domains:
        # 2. 获取现有记录
        old_records = get_dns_records(domain)
        old_count = len(old_records)
        
        actions = {"update": 0, "create": 0, "delete": 0}
        
        # 3. 逻辑处理
        # 情况 A: IP 多于 现有记录 -> 更新现有 + 创建多出的
        # 情况 B: IP 少于 现有记录 -> 更新匹配的 + 删除多余的
        
        max_idx = max(ip_count, old_count)
        
        for i in range(max_idx):
            if i < ip_count and i < old_count:
                # 更新
                if old_records[i]['content'] != new_ips[i]:
                    if update_dns_record(old_records[i]['id'], domain, new_ips[i]):
                        actions["update"] += 1
                else:
                    pass # 内容一致跳过
            elif i < ip_count:
                # 现有坑位不够，创建新坑位
                if create_dns_record(domain, new_ips[i]):
                    actions["create"] += 1
            elif i < old_count:
                # 优选 IP 变少了，删除多余坑位
                if delete_dns_record(old_records[i]['id']):
                    actions["delete"] += 1
            
            time.sleep(0.5)

        # 4. 组装单个域名的报告
        domain_status = f"### 🌐 域名: {domain}\n"
        domain_status += f"- **获取优选 IP**: `{ip_count}` 个\n"
        domain_status += f"- **原有解析坑位**: `{old_count}` 个\n"
        domain_status += f"- **执行操作**: 更新 `{actions['update']}`，新增 `{actions['create']}`，删除 `{actions['delete']}`\n"
        domain_status += f"- **最终生效数量**: `{ip_count}` 个\n\n"
        report.append(domain_status)

    # 5. 发送汇总推送
    if report:
        push_plus("CF 优选自动伸缩报告", "".join(report))

if __name__ == '__main__':
    main()
