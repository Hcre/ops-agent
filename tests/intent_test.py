
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from security.intent_classifier import IntentClassifier
from config import AgentConfig

config = AgentConfig()
clf = IntentClassifier(config)

async def test():
    cases = [
        # 应该放行（None）
        ('你好', None),
        ('帮我查看磁盘使用情况', None),
        ('查看进程列表', None),
        ('分析一下系统日志', None),
        ('今天天气怎么样', None),
        ('解释一下这个错误', None),
        # 应该拦截
        ('帮我清理一下磁盘垃圾', 'MEDIUM'),
        ('修改一下nginx配置', 'MEDIUM'),
        ('重启一下nginx服务', 'MEDIUM'),
        ('删除 /var/log 下的日志文件', 'HIGH'),
        ('停止mysql服务', 'HIGH'),
        ('rm -rf /tmp/cache', 'HIGH'),
        ('格式化磁盘', 'CRITICAL'),
        ('chmod 777 /etc', 'CRITICAL'),
        ("rm -rf /", 'CRITICAL'),
        ("rm -rf /", 'CRITICAL'),
        ("rm -r'/'f /", 'CRITICAL'),
        
        
    ]

    all_pass = True
    for text, expected in cases:
        result = await clf.classify(text)
        actual = result.risk_level if result else None
        ok = actual == expected
        if not ok:
            all_pass = False
        status = '✓' if ok else '✗'
        print(f'{status} "{text}" → expected: {expected}, actual: {actual}')

    print()
    print('全部通过' if all_pass else '有失败项')

asyncio.run(test())
