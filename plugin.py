import re
import json
import random
import requests
import logging
from typing import List, Tuple, Type, Optional

from src.plugin_system.base.base_plugin import BasePlugin, register_plugin
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.common.logger import get_logger
from src.plugin_system import BaseCommand
from src.plugin_system.apis import person_api

logger = get_logger("poke_plugin")

# 调试开关
POKE_DEBUG = False

# Napcat 配置
NAPCAT_HOST = "127.0.0.1"
NAPCAT_PORT = "4999"
NAPCAT_BASE_URL = f"http://{NAPCAT_HOST}:{NAPCAT_PORT}"


class PokeAction(BaseAction):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api = kwargs.get('api', None)
        self.reasoning = kwargs.get('reasoning', None)


    """戳一戳Action - 可被主动调用或LLM判定自动触发"""

    action_name = "poke"
    action_description = "调用QQ戳一戳功能"

    focus_activation_type = ActionActivationType.LLM_JUDGE
    normal_activation_type = ActionActivationType.KEYWORD
    mode_enable = ChatMode.ALL
    parallel_action = False

    activation_keywords = ["戳我", "戳一下", "poke"]
    keyword_case_sensitive = False

    associated_types = ["text"]

    llm_judge_prompt = """
你是一个会戳别人的小助手。戳一戳是一个相对友善的动作，在聊天时会时不时的触发
当用户表示想让bot戳人，或者有人戳或者揉了bot，或者你想要戳别人时你可以调用。

使用戳一戳的情况：
1. 用户请求“戳我”、“戳一下”
2. bot被戳或揉，用户希望“戳回去”
3. 用户调戏bot或提出请求需要bot回应
4. 友好的聊天时使用
5. 别人被戳或者揉时跟着使用

不使用戳一戳的情况：
1 连续戳了超过三次以上

"""

    action_parameters = {
        "user_id": "要戳的用户ID，必填",
        "group_id": "群ID（可选）",
        "reply_id": "回复消息ID（可选）",
        "poke_mode": "主动或被动，默认为被动",
    }

    action_require = [
        "当你想使用QQ戳一戳功能时",
        "当别人叫你戳他时偶尔使用",
        "当你被揉或者戳之后可以使用",
        "友好聊天的氛围时偶尔使用",
        "提及某人时偶尔使用",
        "别人被戳或者揉时偶尔跟着使用",
        "当你觉得对方很可爱时偶尔使用",
        "当你想和对方亲近时偶尔使用",
        "当你想安慰对方时使用",
        "注意：如果你已经戳过某人了，就不要再次戳了，不然会引起别人的反感！！！不要连续使用！！"
    ]

    last_poke_user: Optional[str] = None
    last_poke_group: Optional[str] = None

    async def execute(self) -> Tuple[bool, str]:
        user_id_or_name = self.action_data.get("user_id")
        group_id = self.action_data.get("group_id")
        reply_id = self.action_data.get("reply_id")
        poke_mode = self.action_data.get("poke_mode", "被动")

        if POKE_DEBUG:
            logger.info(f"poke参数: user_id={user_id_or_name}, group_id={group_id}, reply_id={reply_id}, poke_mode={poke_mode}")

        if not user_id_or_name:
            await self.send_text("戳一戳需要user_id")
            return False, "戳一戳需要user_id"

        # 判断 user_id_or_name 是否是数字ID，如果不是调用接口转换
        if not str(user_id_or_name).isdigit():
            try:
                #platform, user_id = await self.api.get_user_id_by_person_name(user_id_or_name)
                person_id = person_api.get_person_id_by_name(user_id_or_name)
                user_id = await person_api.get_person_value(person_id, "user_id")
            except Exception as e:
                logger.error(f"{self.log_prefix} 查找用户ID时出错: {e}")
                await self.send_text("查找用户信息时出现问题~")
                return False, "查找用户信息时出现问题"

            if not user_id:
                await self.send_text(f"找不到用户 {user_id_or_name} 的ID")
                return False, "用户不存在"
        else:
            user_id = user_id_or_name

        # 执行戳一戳请求，群聊或私聊分开处理
        if group_id:
            ok, result = self._send_group_poke(group_id, reply_id, user_id)
        else:
            ok, result = self._send_friend_poke(user_id)

        if ok:
            if POKE_DEBUG:
                await self.send_text(f"戳一戳成功: {result}")
            #else:
                #await self.send_text("戳一戳成功~")
            #await self.send_text(f"戳一戳成功: {result}")
            return True, "戳一戳成功"
        else:
            await self.send_text(f"戳一戳失败: {result}")
            return False, f"戳一戳失败: {result}"

    def _send_group_poke(self, group_id: str, reply_id: Optional[str], user_id: str):
        url = f"{NAPCAT_BASE_URL}/send_group_msg"
        message = []
        if reply_id:
            try:
                reply_int = int(reply_id)
            except Exception:
                reply_int = None
            if reply_int:
                message.append({"type": "reply", "data": {"id": reply_int}})
        message.append({"type": "poke", "data": {"qq": int(user_id)}})
        payload = {"group_id": int(group_id), "message": message}
        return self._send_request(url, payload)

    def _send_friend_poke(self, target_id: str):
        url = f"{NAPCAT_BASE_URL}/friend_poke"
        payload = {"user_id": int(target_id), "target_id": int(target_id)}
        return self._send_request(url, payload)

    def _send_request(self, url, payload):
        
        payload["group_id"] = "这里填你要让bot戳一戳的群号"    #########################群号这边改！！！！#################
        headers = {"Content-Type": "application/json"}

        try:
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            return True, response.json()
        except Exception as e:
            logger.error(f"[戳一戳请求失败] {e}")
            return False, str(e)
        

class PassivePokeCommand(BaseCommand):
    """被动响应戳一戳的Command组件"""
    command_name = "passive_poke"
    command_description = "被动响应戳一戳命令"
    command_pattern = r".*这是QQ的一个功能，用于提及某人，但没那么明显.*"
    command_help = "触发被动戳一戳"
    command_examples = ["这是QQ的一个功能，用于提及某人，但没那么明显"]
    intercept_message = False

    async def execute(self) -> Tuple[bool, str]:
        message_text = getattr(self.message, 'text', None)
        sender = getattr(self.message.message_info, 'user_info', None)
        if not sender or not hasattr(sender, 'user_id'):
            return False, "无法获取发送者user_id"
        poke_target_id = sender.user_id
        in_group = False
        group_id = None
        if hasattr(self.message.message_info, 'group_info') and getattr(self.message.message_info.group_info, 'group_id', None):
            in_group = True
            group_id = self.message.message_info.group_info.group_id
        if in_group:
            success, result = self.send_group_poke(group_id, poke_target_id)
        else:
            success, result = self.send_friend_poke(poke_target_id, poke_target_id)
        if success:
            logger.info(f"被动戳戳反击成功: {result}")
            return True, "被动戳戳反击成功"
        else:
            logger.error(f"被动戳戳反击失败: {result!r}")
            error_msg = f"被动戳戳反击失败: {result.get('error_message', str(result)) if isinstance(result, dict) else str(result) or '未知错误'}"
            return False, error_msg

    def send_group_poke(self, group_id, user_id):
        if group_id == 961371416:
            if POKE_DEBUG:
                debug_msg = f"检测到特殊群号961371416，跳过群聊戳一戳，group_id: {group_id}, user_id: {user_id}"
                return False, debug_msg
        
        url = f"{NAPCAT_BASE_URL.rstrip('/')}/group_poke"
        payload = json.dumps({
            "group_id": group_id,
            "user_id": user_id
        })
        headers = {"Content-Type": "application/json"}
        debug_msgs = []
        if POKE_DEBUG:
            debug_msgs.append(f"群聊戳一戳请求头: {headers}, group_id: {group_id}, user_id: {user_id}")
        try:
            response = requests.post(url, data=payload, headers=headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            if POKE_DEBUG:
                debug_msgs.append(f"群聊戳一戳成功! 状态码: {response.status_code}, 响应: {data}")
            return data.get("status") == "ok", '\n'.join(debug_msgs + [str(data)])
        except requests.exceptions.RequestException as e:
            error_info = {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "response_status": getattr(e.response, 'status_code', None) if hasattr(e, 'response') else None,
                "response_text": getattr(e.response, 'text', None) if hasattr(e, 'response') else None
            }
            debug_msgs.append(f"群聊戳一戳失败: {error_info}")
            return False, '\n'.join(debug_msgs)
        except Exception as e:
            error_info = {
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
            debug_msgs.append(f"群聊戳一戳异常: {error_info}")
            return False, '\n'.join(debug_msgs)

    def send_friend_poke(self, user_id, target_id):
        import http.client
        import json
        conn = http.client.HTTPConnection(NAPCAT_HOST, NAPCAT_PORT)
        payload = json.dumps({
            "user_id": user_id,
            "target_id": target_id
        })
        headers = {"Content-Type": "application/json"}
        debug_msgs = []
        if POKE_DEBUG:
            debug_msgs.append(f"私聊戳一戳请求头: {headers}, user_id: {user_id}, target_id: {target_id}")
        try:
            conn.request("POST", "/friend_poke", payload, headers)
            res = conn.getresponse()
            data = res.read()
            result = data.decode("utf-8")
            if POKE_DEBUG:
                debug_msgs.append(f"私聊戳一戳成功! 响应: {result}")
            try:
                data_json = json.loads(result)
                return data_json.get("status") == "ok", '\n'.join(debug_msgs + [str(data_json)])
            except Exception:
                return True, '\n'.join(debug_msgs + [result])
        except Exception as e:
            error_info = {
                "error_type": type(e).__name__,
                "error_message": str(e)
            }
            debug_msgs.append(f"私聊戳一戳异常: {error_info}")
            return False, '\n'.join(debug_msgs)
        

    # ===== 外部接口 =====

    @classmethod
    def handle_log(cls, log_text: str):
        match = re.search(r"用户\[(\d+)\](?:在群\[(\d+)\])?戳了bot", log_text)
        if match:
            cls.last_poke_user = match.group(1)
            cls.last_poke_group = match.group(2)
        if any(kw in log_text for kw in ["戳回去", "揉回去"]):
            cls.auto_poke_last_user()

    @classmethod
    def handle_user_message(cls, user_id: str, group_id: Optional[str], text: str, reply_id: Optional[int] = None):
        if re.search(r"戳我", text):
            import asyncio
            action_data = {
                "user_id": user_id,
                "poke_mode": "主动",
                "reply_id": reply_id,
                "chat_mode": ChatMode.GROUP if group_id else ChatMode.PRIVATE
            }
            if group_id:
                action_data["group_id"] = group_id
            action = cls(action_data=action_data)
            asyncio.create_task(action.execute())

    @classmethod
    def auto_poke_last_user(cls):
        if not cls.last_poke_user:
            return
        import asyncio
        action_data = {
            "user_id": cls.last_poke_user,
            "chat_mode": ChatMode.GROUP if cls.last_poke_group else ChatMode.PRIVATE
        }
        if cls.last_poke_group:
            action_data["group_id"] = cls.last_poke_group
        action = cls(action_data=action_data)
        asyncio.create_task(action.execute())


@register_plugin
class PokePlugin(BasePlugin):
    plugin_name = "poke_plugin"
    plugin_description = "QQ戳一戳插件：支持主动、被动、戳回去功能"
    plugin_version = "0.3.3"
    plugin_author = "何夕"
    enable_plugin = True
    config_file_name = "config.toml"

    config_section_descriptions = {
        "plugin": "插件基本信息配置",
        "poke": "戳一戳功能配置",
    }

    config_schema = {
        "plugin": {
            "name": ConfigField(str, default="poke_plugin", description="插件名称"),
            "enabled": ConfigField(bool, default=True, description="是否启用插件"),
            "version": ConfigField(str, default="1.0.0", description="插件版本"),
            "description": ConfigField(str, default="QQ戳一戳插件", description="插件描述"),
        },
        "poke": {
            "napcat_host": ConfigField(str, default="127.0.0.1", description="Napcat Host"),
            "napcat_port": ConfigField(str, default="4999", description="Napcat Port"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (PokeAction.get_action_info(), PokeAction),
            #(PassivePokeCommand.get_command_info(), PassivePokeCommand),
        ]
    
