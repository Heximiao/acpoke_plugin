import re
import json
import random
import requests
import logging
from typing import List, Tuple, Type, Optional

from src.plugin_system import BasePlugin, register_plugin, BaseCommand
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.apis import person_api
from src.common.logger import get_logger


logger = get_logger("poke_plugin")

# 调试开关
POKE_DEBUG = True

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
        #"当别人被戳或者揉或者捏或者拍时使用"
        "当你想安慰对方时使用",
        "注意：如果你已经戳过某人了，就不要再次戳了，不然会引起别人的反感！！！不要连续使用！！"
    ]

    last_poke_user: Optional[str] = None
    last_poke_group: Optional[str] = None

    async def get_group_id(self) -> Optional[str]:
        """获取当前上下文的群ID"""
        group_id = self.action_data.get("group_id")

        # 1. 从 message.message_info.group_id 获取
        if not group_id and hasattr(self, "message") and getattr(self.message, "message_info", None):
            group_id = getattr(self.message.message_info, "group_id", None)

        # 2. 从 chat_stream 获取
        if not group_id and hasattr(self, "chat_stream") and getattr(self.chat_stream, "group_id", None):
            group_id = self.chat_stream.group_id

        # 3. 从自身属性获取
        if not group_id and hasattr(self, "group_id"):
            group_id = getattr(self, "group_id", None)

        return group_id

    async def execute(self) -> Tuple[bool, str]:
        user_id_or_name = self.action_data.get("user_id")
        reply_id = self.action_data.get("reply_id")
        poke_mode = self.action_data.get("poke_mode", "被动")

        # 🔑 新增：动态获取 group_id
        group_id = await self.get_group_id()

        if POKE_DEBUG:
            logger.info(f"poke参数: user_id={user_id_or_name}, group_id={group_id}, reply_id={reply_id}, poke_mode={poke_mode}")

        if not user_id_or_name:
            await self.send_text("戳一戳需要user_id")
            return False, "戳一戳需要user_id"

        # 用户名 → QQ号的逻辑保持不动
        if not str(user_id_or_name).isdigit():
            try:
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

        # 执行戳一戳请求
        if group_id:
            ok, result = self._send_group_poke(group_id, reply_id, user_id)
        else:
            ok, result = self._send_friend_poke(user_id)

        if ok:
            return True, "戳一戳成功"
        else:
            await self.send_text(f"戳一戳失败: {result}")
            return False, f"戳一戳失败: {result}"

    def _send_group_poke(self, group_id: Optional[str], reply_id: Optional[int], user_id: str):
        # 如果 group_id 无效，使用默认群号
        if not group_id or not str(group_id).isdigit():
            logger.warning(f"[poke_plugin] 无效的 group_id={group_id}，使用默认群号 961371416")
            group_id = "961371416"

        url = f"{NAPCAT_BASE_URL}/group_poke"
        payload = {
            "group_id": int(group_id),
            "user_id": int(user_id)
        }

        if POKE_DEBUG:
            logger.info(f"[poke_plugin] 发起群聊戳一戳: {payload}")

        try:
            response = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=5)
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "ok", data
        except Exception as e:
            logger.error(f"[戳一戳请求失败] {e}")
            return False, str(e)

    def _send_friend_poke(self, target_id: str):
        url = f"{NAPCAT_BASE_URL}/friend_poke"
        payload = {"user_id": int(target_id), "target_id": int(target_id)}
        return self._send_request(url, payload)

    def _send_request(self, url, payload):
        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            return True, response.json()
        except Exception as e:
            logger.error(f"[戳一戳请求失败] {e}")
            return False, str(e)


@register_plugin
class PokePlugin(BasePlugin):
    plugin_name: str = "poke_plugin"
    plugin_description = "QQ戳一戳插件：支持主动、被动、戳回去功能"
    plugin_version = "0.2.0"
    plugin_author = "何夕"
    enable_plugin: bool = True
    config_file_name: str = "config.toml"
    dependencies: list[str] = []
    python_dependencies: list[str] = []

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
        ]
