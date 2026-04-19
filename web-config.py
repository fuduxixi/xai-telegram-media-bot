#!/usr/bin/env python3
"""
作者：by fuduxixi

美观安全的 Web 配置界面 - 最终版
包含：
- 漂亮登录页 + 深空背景
- 动态 XAI_API_KEYS 多行增删
- 实时日志自动刷新（可开关）
- 清空日志按钮
- 保存后自动热重载（通过共享信号文件）
- Bot 当前在线状态展示
"""

import os
import signal
import subprocess
import json
from pathlib import Path
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, session, flash, jsonify

app = Flask(__name__)

ENV_FILE = Path('.env')
ADMIN_USER = os.getenv('ADMIN_USER', 'admin')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', 'admin123')
SERVICE_NAME = 'xai-telegram-media-bot'
WEB_PORT = int(os.getenv('WEB_CONFIG_PORT', '5000'))
LOG_FILE = Path(os.getenv('BOT_LOG_FILE', 'logs/bot.log'))
STATUS_FILE = Path(os.getenv('BOT_STATUS_FILE', 'data/bot-status.json'))
RELOAD_SIGNAL_FILE = Path(os.getenv('BOT_RELOAD_SIGNAL_FILE', 'data/reload-bot.signal'))
WEB_SECRET_KEY = os.getenv('WEB_SECRET_KEY', '').strip()

if not WEB_SECRET_KEY:
    raise RuntimeError('缺少 WEB_SECRET_KEY：请先在 .env 中设置一个高强度随机密钥，再启动 web-config.py')

app.secret_key = WEB_SECRET_KEY


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return func(*args, **kwargs)
    return wrapper


def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env[key.strip()] = value.strip().strip('"\'')
    return env


def save_env(new_values):
    existing_lines = []
    if ENV_FILE.exists():
        existing_lines = ENV_FILE.read_text(encoding='utf-8').splitlines()

    preserved = []
    replaced_keys = set(new_values.keys())
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith('#') and '=' in stripped:
            key = stripped.split('=', 1)[0].strip()
            if key in replaced_keys:
                continue
        preserved.append(line)

    for key, value in new_values.items():
        if value:
            preserved.append(f'{key}={value}')

    ENV_FILE.write_text('\n'.join(preserved).strip() + '\n', encoding='utf-8')


def parse_xai_api_keys(form):
    values = []
    for item in form.getlist('xai_api_keys[]'):
        item = item.strip()
        if item:
            values.append(item)
    return ','.join(values)


def get_log_text(level='all'):
    try:
        if LOG_FILE.exists():
            lines = LOG_FILE.read_text(encoding='utf-8', errors='replace').splitlines()
            if level == 'error':
                lines = [line for line in lines if 'ERROR' in line or 'Traceback' in line or 'RuntimeError' in line]
            elif level == 'progress':
                lines = [line for line in lines if 'progress |' in line or 'enqueue |' in line or '视频生成完成' in line or '图生视频提交成功' in line]
            return '\n'.join(lines[-300:]) if lines else '暂无匹配日志'
        return f'日志文件不存在: {LOG_FILE}'
    except Exception as exc:
        return f'读取日志失败: {exc}'


def clear_log_text():
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text('', encoding='utf-8')
        return True, '日志已清空'
    except Exception as exc:
        return False, f'清空日志失败: {exc}'


def hot_reload_bot():
    try:
        RELOAD_SIGNAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        RELOAD_SIGNAL_FILE.write_text('reload\\n', encoding='utf-8')
        return True, '已触发热重载信号，bot 将自动重启'
    except Exception as exc:
        return False, f'热重载信号写入失败: {exc}'


def can_reload_bot(status: dict) -> tuple[bool, str]:
    state = str(status.get('state', 'unknown')).strip().lower()
    queue_size_raw = status.get('queue_size', 0)
    try:
        queue_size = int(queue_size_raw)
    except (TypeError, ValueError):
        queue_size = 0

    if state in {'busy', 'reloading', 'stopping'}:
        return False, f'bot 当前状态为 {state}，存在任务执行中，暂不允许重启'
    if queue_size > 0:
        return False, f'bot 当前还有 {queue_size} 个排队任务，暂不允许重启'
    return True, ''


def get_bot_status():
    try:
        if STATUS_FILE.exists():
            return json.loads(STATUS_FILE.read_text(encoding='utf-8'))
    except Exception:
        pass
    return {
        'state': 'unknown',
        'bot_name': '-',
        'bot_username': '-',
        'queue_size': '-',
        'last_result': '-',
        'updated_at': 0,
    }


LOGIN_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>xAI Bot 登录</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap');
        body {
            background: linear-gradient(135deg, #0f172a 0%, #1e2937 100%);
            font-family: 'Inter', system-ui, sans-serif;
        }
        .title { font-family: 'Space Grotesk', sans-serif; }
        .glass {
            background: rgba(255,255,255,0.08);
            backdrop-filter: blur(18px);
            border: 1px solid rgba(255,255,255,0.12);
        }
        .input {
            background: rgba(15, 23, 42, 0.72);
            border: 1px solid rgba(148, 163, 184, 0.25);
        }
        .input:focus {
            border-color: rgb(59 130 246);
            box-shadow: 0 0 0 3px rgba(59,130,246,0.18);
            outline: none;
        }
    </style>
</head>
<body class="min-h-screen text-white flex items-center justify-center px-6 py-12">
    <div class="w-full max-w-5xl grid lg:grid-cols-2 gap-8 items-stretch">
        <div class="hidden lg:flex flex-col justify-between glass rounded-[32px] p-10 overflow-hidden relative">
            <div>
                <div class="inline-flex items-center gap-3 px-4 py-2 rounded-full bg-white/10 mb-6">
                    <span class="text-xl">🤖</span>
                    <span class="text-sm text-slate-300">xAI Telegram Media Bot</span>
                </div>
                <h1 class="title text-5xl leading-tight font-semibold tracking-tight">简简单单Telegram 机器人配置中心</h1>
                <p class="mt-6 text-slate-300 text-lg leading-8">
                    登录后可管理 API Key、默认模型、审核重试策略、查看日志、重启服务。
                </p>
            </div>
            <div class="grid grid-cols-2 gap-4 mt-10 text-sm">
                <div class="glass rounded-2xl p-5">
                    <div class="text-slate-400 mb-2">支持能力</div>
                    <div class="font-medium">图片 / 视频 / 图生视频</div>
                </div>
                <div class="glass rounded-2xl p-5">
                    <div class="text-slate-400 mb-2">安全能力</div>
                    <div class="font-medium">审核拒绝自动安全改写</div>
                </div>
                <div class="glass rounded-2xl p-5">
                    <div class="text-slate-400 mb-2">运维能力</div>
                    <div class="font-medium">日志查看 / 一键重启</div>
                </div>
                <div class="glass rounded-2xl p-5">
                    <div class="text-slate-400 mb-2">部署能力</div>
                    <div class="font-medium">Docker 一键部署</div>
                </div>
            </div>
        </div>

        <div class="glass rounded-[32px] p-8 md:p-10 self-center">
            <div class="mb-8">
                <div class="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-violet-600 flex items-center justify-center text-3xl mb-5">🔐</div>
                <h2 class="title text-4xl font-semibold tracking-tight">登录配置中心</h2>
                <p class="text-slate-400 mt-3">使用管理员账号登录后，才能查看和修改机器人配置。</p>
            </div>

            {% with messages = get_flashed_messages() %}
              {% if messages %}
                <div class="mb-6 rounded-2xl border border-red-500/40 bg-red-500/10 px-5 py-4 text-red-300 text-sm">
                  {% for message in messages %}{{ message }}{% endfor %}
                </div>
              {% endif %}
            {% endwith %}

            <form method="post" class="space-y-5">
                <div>
                    <label class="block text-sm text-slate-400 mb-2">用户名</label>
                    <input class="input w-full rounded-2xl px-5 py-4" type="text" name="username" value="admin" autocomplete="username">
                </div>
                <div>
                    <label class="block text-sm text-slate-400 mb-2">密码</label>
                    <input class="input w-full rounded-2xl px-5 py-4" type="password" name="password" autocomplete="current-password">
                </div>
                <button type="submit" class="w-full py-4 rounded-2xl bg-gradient-to-r from-blue-600 via-violet-600 to-fuchsia-600 hover:brightness-110 transition text-lg font-semibold shadow-xl shadow-violet-900/30">
                    登录
                </button>
            </form>
        </div>
    </div>
</body>
</html>
"""


CONFIG_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>xAI Telegram Bot · 配置中心</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap');
        body { background: linear-gradient(135deg, #0f172a 0%, #1e2937 100%); font-family: 'Inter', system-ui, sans-serif; }
        .title { font-family: 'Space Grotesk', sans-serif; }
        .glass { background: rgba(255,255,255,0.08); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); }
        .input { background: rgba(15,23,42,0.6); border: 1px solid rgba(148,163,184,0.2); }
        .input:focus { border-color: rgb(59 130 246); box-shadow: 0 0 0 3px rgba(59,130,246,0.2); outline: none; }
    </style>
</head>
<body class="min-h-screen text-white">
    <div class="max-w-5xl mx-auto p-8">
        <div class="flex justify-between items-center mb-12">
            <div class="flex items-center gap-4">
                <div class="w-12 h-12 bg-gradient-to-br from-blue-500 to-purple-600 rounded-2xl flex items-center justify-center text-3xl">🤖</div>
                <div>
                    <h1 class="title text-5xl font-semibold tracking-tighter">xAI Bot</h1>
                    <p class="text-slate-400 -mt-1">配置管理中心</p>
                </div>
            </div>
            <a href="/logout" class="px-6 py-3 bg-white/10 hover:bg-white/20 rounded-2xl transition">退出登录</a>
        </div>

        {% with messages = get_flashed_messages() %}
          {% if messages %}
            <div class="mb-8 p-5 bg-emerald-500/10 border border-emerald-500 rounded-3xl text-emerald-300">
              {% for message in messages %}<div>{{ message }}</div>{% endfor %}
            </div>
          {% endif %}
        {% endwith %}

        {% if success %}
        <div class="mb-8 p-5 bg-emerald-500/10 border border-emerald-500 rounded-3xl text-emerald-400 flex items-center gap-3">
            <span class="text-2xl">✓</span>
            <span class="font-medium">配置已保存并已触发热重载</span>
        </div>
        {% endif %}

        <!-- Bot Status -->
        <div class="glass rounded-3xl p-8 mb-10">
            <div class="flex items-center gap-3 mb-6">
                <div class="text-3xl">🟢</div>
                <h2 class="text-2xl font-semibold">Bot 当前状态</h2>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div class="bg-slate-900/50 rounded-2xl p-5">
                    <div class="text-slate-400 mb-1">运行状态</div>
                    <div class="text-lg font-medium">{{ status.get('state', 'unknown') }}</div>
                </div>
                <div class="bg-slate-900/50 rounded-2xl p-5">
                    <div class="text-slate-400 mb-1">Bot 用户名</div>
                    <div class="text-lg font-medium">@{{ status.get('bot_username', '-') }}</div>
                </div>
                <div class="bg-slate-900/50 rounded-2xl p-5">
                    <div class="text-slate-400 mb-1">队列长度</div>
                    <div class="text-lg font-medium">{{ status.get('queue_size', '-') }}</div>
                </div>
                <div class="bg-slate-900/50 rounded-2xl p-5">
                    <div class="text-slate-400 mb-1">默认视频/图生视频数量</div>
                    <div class="text-lg font-medium">{{ status.get('video_count', env.get('XAI_VIDEO_DEFAULT_N', '1')) }}</div>
                </div>
                <div class="bg-slate-900/50 rounded-2xl p-5 md:col-span-2">
                    <div class="text-slate-400 mb-1">最近结果</div>
                    <div class="text-lg font-medium break-all">{{ status.get('last_result', '-') }}</div>
                </div>
            </div>
        </div>

        <form id="config-form" method="post" class="space-y-10">
            <div class="glass rounded-3xl p-8">
                <div class="flex items-center gap-3 mb-6">
                    <div class="text-3xl">📬</div>
                    <h2 class="text-2xl font-semibold">Telegram 设置</h2>
                </div>
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">BOT TOKEN</label>
                        <input type="text" name="TELEGRAM_BOT_TOKEN" value="{{ env.get('TELEGRAM_BOT_TOKEN','') }}" class="input w-full rounded-2xl px-6 py-5 text-lg" required>
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">允许的用户ID（留空 = 所有人可用）</label>
                        <input type="text" name="TG_ALLOWED_USER_IDS" value="{{ env.get('TG_ALLOWED_USER_IDS','') }}" class="input w-full rounded-2xl px-6 py-5 text-lg">
                    </div>
                </div>
            </div>

            <div class="glass rounded-3xl p-8">
                <div class="flex items-center gap-3 mb-6">
                    <div class="text-3xl">🔑</div>
                    <h2 class="text-2xl font-semibold">xAI API 设置</h2>
                </div>
                <div class="space-y-6">
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">XAI_API_KEY（推荐）</label>
                        <input type="text" name="XAI_API_KEY" value="{{ env.get('XAI_API_KEY','') }}" class="input w-full rounded-2xl px-6 py-5 text-lg">
                    </div>
                    <div>
                        <div class="flex justify-between items-center mb-3">
                            <label class="block text-sm text-slate-400 font-medium">XAI_API_KEYS（一行一个）</label>
                            <button type="button" onclick="addApiKeyRow()" class="px-5 py-2 text-sm bg-blue-500 hover:bg-blue-600 rounded-2xl">+ 新增一行</button>
                        </div>
                        <div id="xai-api-keys-container" class="space-y-3">
                            {% set keys_raw = env.get('XAI_API_KEYS', '') %}
                            {% set keys = keys_raw.split(',') if keys_raw else [] %}
                            {% set nonempty = namespace(count=0) %}
                            {% for key in keys %}
                            {% if key.strip() %}
                            {% set nonempty.count = nonempty.count + 1 %}
                            <div class="flex gap-3 api-key-row">
                                <input type="text" name="xai_api_keys[]" value="{{ key.strip() }}" class="input flex-1 rounded-2xl px-6 py-4 text-lg">
                                <button type="button" onclick="removeApiKeyRow(this)" class="px-5 py-4 bg-red-500/20 hover:bg-red-500/30 rounded-2xl text-red-400">删除</button>
                            </div>
                            {% endif %}
                            {% endfor %}
                            {% if nonempty.count == 0 %}
                            <div class="flex gap-3 api-key-row">
                                <input type="text" name="xai_api_keys[]" value="" class="input flex-1 rounded-2xl px-6 py-4 text-lg" placeholder="输入 xAI API Key">
                                <button type="button" onclick="removeApiKeyRow(this)" class="px-5 py-4 bg-red-500/20 hover:bg-red-500/30 rounded-2xl text-red-400">删除</button>
                            </div>
                            {% endif %}
                        </div>
                    </div>
                </div>
            </div>

            <div class="glass rounded-3xl p-8">
                <div class="flex items-center gap-3 mb-6">
                    <div class="text-3xl">🖼️</div>
                    <h2 class="text-2xl font-semibold">图片生成默认设置</h2>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">默认模型</label>
                        <select name="XAI_IMAGE_MODEL" class="input w-full rounded-2xl px-6 py-5 text-lg">
                            <option value="grok-imagine-image" {% if env.get('XAI_IMAGE_MODEL') == 'grok-imagine-image' %}selected{% endif %}>grok-imagine-image（推荐）</option>
                            <option value="grok-imagine-image-pro" {% if env.get('XAI_IMAGE_MODEL') == 'grok-imagine-image-pro' %}selected{% endif %}>grok-imagine-image-pro（更强）</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">默认比例</label>
                        <input type="text" name="XAI_IMAGE_DEFAULT_RATIO" value="{{ env.get('XAI_IMAGE_DEFAULT_RATIO','1:1') }}" class="input w-full rounded-2xl px-6 py-5 text-lg">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">默认生成数量 (1-4)</label>
                        <input type="number" name="XAI_IMAGE_DEFAULT_N" value="{{ env.get('XAI_IMAGE_DEFAULT_N','1') }}" min="1" max="4" class="input w-full rounded-2xl px-6 py-5 text-lg">
                    </div>
                </div>
            </div>

            <div class="glass rounded-3xl p-8">
                <div class="flex items-center gap-3 mb-6">
                    <div class="text-3xl">🎥</div>
                    <h2 class="text-2xl font-semibold">视频 / 图生视频设置</h2>
                </div>
                <div class="space-y-6">
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label class="block text-sm text-slate-400 mb-2 font-medium">默认生成数量（文生视频 / 图生视频，1-4）</label>
                            <input type="number" name="XAI_VIDEO_DEFAULT_N" value="{{ env.get('XAI_VIDEO_DEFAULT_N','1') }}" min="1" max="4" class="input w-full rounded-2xl px-6 py-5 text-lg">
                        </div>
                        <div>
                            <label class="block text-sm text-slate-400 mb-2 font-medium">最大生成数量（文生视频 / 图生视频，1-4）</label>
                            <input type="number" name="XAI_VIDEO_MAX_N" value="{{ env.get('XAI_VIDEO_MAX_N','4') }}" min="1" max="4" class="input w-full rounded-2xl px-6 py-5 text-lg">
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm text-slate-400 mb-2 font-medium">审核自动改写模式</label>
                        <select name="VIDEO_REWRITE_MODE" class="input w-full rounded-2xl px-6 py-5 text-lg">
                            <option value="off" {% if env.get('VIDEO_REWRITE_MODE', '') == 'off' or (not env.get('VIDEO_REWRITE_MODE') and env.get('VIDEO_AUTO_REWRITE_ON_MODERATION','0') not in ['1','true']) %}selected{% endif %}>关闭</option>
                            <option value="mild" {% if env.get('VIDEO_REWRITE_MODE', '') == 'mild' or (not env.get('VIDEO_REWRITE_MODE') and env.get('VIDEO_AUTO_REWRITE_ON_MODERATION','0') in ['1','true']) %}selected{% endif %}>mild（轻度改写，尽量保留原意）</option>
                            <option value="strong" {% if env.get('VIDEO_REWRITE_MODE', '') == 'strong' %}selected{% endif %}>strong（强力改写，更保守）</option>
                        </select>
                    </div>
                    <div class="text-sm text-slate-400 leading-7">
                        <div>这组数量配置同时作用于 <code>/video</code> 和 <code>/img2video</code>。</div>
                        <div>命令行也支持单次覆盖：</div>
                        <div><code>/video -n 3 ...</code> / <code>/img2video -n 2 ...</code></div>
                        <div><code>--safe-rewrite</code> / <code>--safe-rewrite-mild</code> / <code>--safe-rewrite-strong</code> / <code>--no-safe-rewrite</code></div>
                    </div>
                </div>
            </div>

            <div class="h-24"></div>
        </form>

        <button type="submit" form="config-form" class="fixed bottom-8 right-8 z-50 px-8 py-5 text-lg font-semibold bg-gradient-to-r from-blue-600 via-purple-600 to-violet-600 rounded-3xl hover:brightness-110 transition-all duration-200 shadow-2xl shadow-purple-500/40">
            保存并热重载
        </button>

        <div class="mt-16 text-center text-slate-500 text-sm flex flex-col gap-2">
            <div class="flex justify-center gap-4">
                <a href="/logs" class="px-8 py-4 bg-white/10 hover:bg-white/20 rounded-2xl transition flex items-center gap-3">
                    📜 查看实时日志
                </a>
                <a href="/restart" onclick="return confirm('确定要重启机器人吗？')" class="px-8 py-4 bg-orange-500/20 hover:bg-orange-500/30 text-orange-400 rounded-2xl transition flex items-center gap-3">
                    ↻ 重启机器人
                </a>
            </div>
        </div>
    </div>

    <script>
        function addApiKeyRow() {
            const container = document.getElementById('xai-api-keys-container');
            const row = document.createElement('div');
            row.className = 'flex gap-3 api-key-row';
            row.innerHTML = `
                <input type="text" name="xai_api_keys[]" value="" class="input flex-1 rounded-2xl px-6 py-4 text-lg" placeholder="输入 xAI API Key">
                <button type="button" onclick="removeApiKeyRow(this)" class="px-5 py-4 bg-red-500/20 hover:bg-red-500/30 rounded-2xl text-red-400">删除</button>
            `;
            container.appendChild(row);
        }
        function removeApiKeyRow(btn) {
            const container = document.getElementById('xai-api-keys-container');
            if (container.children.length > 1) {
                btn.parentElement.remove();
            } else {
                btn.parentElement.querySelector('input').value = '';
            }
        }
    </script>
</body>
</html>
"""


def render_logs_page(log_content, level='all'):
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>实时日志 - xAI Telegram Bot</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body {{ background: linear-gradient(135deg, #0f172a 0%, #1e2937 100%); font-family: 'Inter', system-ui, sans-serif; }}
        pre {{ font-family: ui-monospace, monospace; white-space: pre-wrap; word-break: break-all; }}
        .glass {{ background: rgba(255,255,255,0.08); backdrop-filter: blur(20px); border: 1px solid rgba(255,255,255,0.1); }}
        .title {{ font-family: 'Space Grotesk', sans-serif; }}
    </style>
</head>
<body class="min-h-screen text-white p-8">
    <div class="max-w-6xl mx-auto">
        <div class="flex flex-wrap justify-between items-center gap-4 mb-8">
            <h1 class="text-4xl font-bold title">实时日志</h1>
            <div class="flex flex-wrap gap-3">
                <a href="/" class="px-6 py-3 bg-white/10 hover:bg-white/20 rounded-2xl transition">← 返回配置</a>
                <button onclick="toggleAutoRefresh()" id="auto-btn" class="px-6 py-3 bg-emerald-500 hover:bg-emerald-600 rounded-2xl transition">自动刷新: 开</button>
                <a href="/logs?level=all" class="px-5 py-3 rounded-2xl transition {'bg-blue-600 text-white' if level=='all' else 'bg-white/10 hover:bg-white/20'}">全部</a>
                <a href="/logs?level=progress" class="px-5 py-3 rounded-2xl transition {'bg-blue-600 text-white' if level=='progress' else 'bg-white/10 hover:bg-white/20'}">只看 progress</a>
                <a href="/logs?level=error" class="px-5 py-3 rounded-2xl transition {'bg-blue-600 text-white' if level=='error' else 'bg-white/10 hover:bg-white/20'}">只看 error</a>
                <a href="/clear-logs" onclick="return confirm('确定要清空日志吗？')" class="px-6 py-3 bg-red-500/20 hover:bg-red-500/30 text-red-400 rounded-2xl transition">清空日志</a>
            </div>
        </div>
        <div class="glass rounded-3xl p-8 border border-white/10">
            <pre id="log-content" class="text-sm text-slate-300 bg-slate-950 p-6 rounded-2xl overflow-auto max-h-[75vh] leading-relaxed">{log_content}</pre>
        </div>
        <div class="text-center text-slate-500 text-xs mt-8">当前筛选：{level} • 仅显示最近 300 行 • 自动刷新已开启</div>
    </div>
    <script>
        let autoRefresh = true;
        const currentLevel = '{level}';
        setInterval(() => {{
            if (autoRefresh) refreshLogs();
        }}, 3000);

        async function refreshLogs() {{
            try {{
                const resp = await fetch('/logs/raw?level=' + encodeURIComponent(currentLevel));
                const data = await resp.json();
                document.getElementById('log-content').textContent = data.logs || '暂无日志';
            }} catch (e) {{}}
        }}

        function toggleAutoRefresh() {{
            autoRefresh = !autoRefresh;
            const btn = document.getElementById('auto-btn');
            btn.textContent = '自动刷新: ' + (autoRefresh ? '开' : '关');
            if (autoRefresh) refreshLogs();
        }}

        window.onload = () => refreshLogs();
    </script>
</body>
</html>
"""


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('username') == ADMIN_USER and request.form.get('password') == ADMIN_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('config'))
        flash('用户名或密码错误')
    return render_template_string(LOGIN_HTML)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/logs')
@login_required
def logs():
    level = request.args.get('level', 'all')
    return render_logs_page(get_log_text(level), level=level)


@app.route('/logs/raw')
@login_required
def logs_raw():
    level = request.args.get('level', 'all')
    return jsonify({'logs': get_log_text(level)})


@app.route('/clear-logs')
@login_required
def clear_logs():
    ok, message = clear_log_text()
    flash(message)
    return redirect(url_for('logs'))


@app.route('/restart')
@login_required
def restart():
    status = get_bot_status()
    allowed, reason = can_reload_bot(status)
    if not allowed:
        flash(reason)
        return redirect(url_for('config'))

    ok, message = hot_reload_bot()
    flash(message)
    return redirect(url_for('config'))


@app.route('/', methods=['GET', 'POST'])
@login_required
def config():
    env = load_env()
    status = get_bot_status()
    success = False

    if request.method == 'POST':
        rewrite_mode = request.form.get('VIDEO_REWRITE_MODE', 'off').strip() or 'off'
        new_values = {
            'TELEGRAM_BOT_TOKEN': request.form.get('TELEGRAM_BOT_TOKEN', '').strip(),
            'XAI_API_KEY': request.form.get('XAI_API_KEY', '').strip(),
            'XAI_API_KEYS': parse_xai_api_keys(request.form),
            'TG_ALLOWED_USER_IDS': request.form.get('TG_ALLOWED_USER_IDS', '').strip(),
            'XAI_IMAGE_MODEL': request.form.get('XAI_IMAGE_MODEL', '').strip(),
            'XAI_IMAGE_DEFAULT_RATIO': request.form.get('XAI_IMAGE_DEFAULT_RATIO', '').strip(),
            'XAI_IMAGE_DEFAULT_N': request.form.get('XAI_IMAGE_DEFAULT_N', '').strip(),
            'XAI_VIDEO_DEFAULT_N': request.form.get('XAI_VIDEO_DEFAULT_N', '').strip(),
            'XAI_VIDEO_MAX_N': request.form.get('XAI_VIDEO_MAX_N', '').strip(),
            'VIDEO_REWRITE_MODE': rewrite_mode,
            'VIDEO_AUTO_REWRITE_ON_MODERATION': '1' if rewrite_mode != 'off' else '0',
        }
        save_env(new_values)
        status = get_bot_status()
        allowed, reason = can_reload_bot(status)
        if allowed:
            ok, message = hot_reload_bot()
            flash(message)
            success = True
        else:
            flash(f'配置已保存，但未自动重启：{reason}')
            success = False
        env = load_env()
        status = get_bot_status()

    return render_template_string(CONFIG_HTML, env=env, status=status, success=success)


if __name__ == '__main__':
    print(f'Web 配置界面已启动 → http://0.0.0.0:{WEB_PORT}')
    print(f'登录用户名: {ADMIN_USER} (密码已隐藏，可在 .env 中修改 ADMIN_PASSWORD)')
    app.run(host='0.0.0.0', port=WEB_PORT, debug=False)
