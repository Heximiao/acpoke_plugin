import re
import json
import random
import requests
import logging
from typing import List, Tuple, Type, Optional
import time

from src.plugin_system import BasePlugin, register_plugin, BaseCommand
from src.plugin_system.base.base_action import BaseAction
from src.plugin_system.base.component_types import ComponentInfo, ActionActivationType, ChatMode
from src.plugin_system.base.config_types import ConfigField
from src.plugin_system.apis import person_api
from src.common.logger import get_logger
from src.plugin_system.apis import database_api


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
        self.llm_response_text = kwargs.get('llm_response_text', "")

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
1 连续戳了超过三次以上,不要连续使用

"""

    action_parameters = {
        "user_id": "要戳的用户ID 也就是名称，必填",
        "group_id": "群ID（不知道就填别填，留空）",
        "reply_id": "回复消息ID",
        "poke_mode": "主动或被动，默认为被动",
    }

    action_require = [
        "当你想使用QQ戳一戳功能时",
        "当别人叫你戳他时偶尔使用",
        "当你被揉或者戳之后可以使用",
        "友好聊天的氛围时使用",
        "提及某人时使用",
        "别人被戳或者揉时跟着使用",
        "当你觉得对方很可爱时使用",
        "当你想和对方亲近时使用",
        "当你想安慰对方时使用",
        "注意：如果你已经戳过某人了，就不要再次戳了，不然会引起别人的反感！！！不要连续使用！！"
    ]

    last_poke_user: Optional[str] = None
    last_poke_group: Optional[str] = None

    async def _napcat_request(self, endpoint: str, payload: dict) -> Optional[dict]:
        """封装Napcat API请求"""
        url = f"{NAPCAT_BASE_URL}/{endpoint}"
        headers = {"Content-Type": "application/json"}
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            data = response.json()
            if data.get("status") == "ok":
                return data
            logger.error(f"Napcat API返回失败: {data}")
        except Exception as e:
            logger.error(f"调用Napcat API失败 ({endpoint}): {e}")
        return None

    async def _get_group_id_from_napcat(self, group_name: str) -> Optional[str]:
        """通过群名模糊查找群ID"""
        data = await self._napcat_request("get_group_list", {})
        if data and isinstance(data.get("data"), list):
            for group in data["data"]:
                if group_name in group.get("group_name", "") or group_name in group.get("group_remark", ""):
                    logger.info(f"通过Napcat API找到群 '{group_name}' 的ID: {group['group_id']}")
                    return str(group['group_id'])
        return None

    async def _get_friend_id_from_napcat(self, friend_name: str) -> Optional[str]:
        """通过好友名模糊查找好友ID"""
        data = await self._napcat_request("get_friend_list", {})
        if data and isinstance(data.get("data"), list):
            for friend in data["data"]:
                if friend_name in friend.get("nickname", "") or friend_name in friend.get("remark", ""):
                    logger.info(f"通过Napcat API找到好友 '{friend_name}' 的ID: {friend['user_id']}")
                    return str(friend['user_id'])
        return None

    async def _get_group_member_id_from_napcat(self, group_id: str, member_name: str) -> Optional[str]:
        """通过群成员名模糊查找成员ID"""
        payload = {"group_id": int(group_id), "no_cache": False}
        data = await self._napcat_request("get_group_member_list", payload)
        if data and isinstance(data.get("data"), list):
            for member in data["data"]:
                if member_name in member.get("nickname", "") or member_name in member.get("card", ""):
                    logger.info(f"在群 {group_id} 中找到成员 '{member_name}' 的ID: {member['user_id']}")
                    return str(member['user_id'])
        return None

    async def get_user_and_group_id(self) -> Tuple[Optional[str], Optional[str]]:
        """从多个来源获取 user_id 和 group_id，优先使用 person_api"""
        user_id_or_name = self.action_data.get("user_id")
        group_id = self.action_data.get("group_id")

        # 1. 优先从上下文获取 group_id
        if not group_id and hasattr(self, "message") and getattr(self.message, "message_info", None):
            group_id = getattr(self.message.message_info, "group_id", None)
        if not group_id and hasattr(self, "chat_stream") and getattr(self.chat_stream, "group_id", None):
            group_id = self.chat_stream.group_id
        if not group_id and hasattr(self, "group_id"):
            group_id = self.group_id
        
        if group_id == 'None':
            group_id = None

        # 2. 如果 user_id_or_name 是纯数字，直接用它
        if user_id_or_name and str(user_id_or_name).isdigit():
            return str(user_id_or_name), group_id

        # 3. 如果 user_id_or_name 是名称，则开始智能查找
        user_id = None
        if user_id_or_name:
            # 优先尝试 person_api
            try:
                person_id = person_api.get_person_id_by_name(user_id_or_name)
                if person_id:
                    user_id = await person_api.get_person_value(person_id, "user_id")
                    if user_id:
                        logger.info(f"查找成功")
                        return user_id, group_id
            except Exception as e:
                logger.error(f"person_api 查找用户ID时出错: {e}")

            # 尝试在当前群聊中查找成员
            if group_id:
                user_id = await self._get_group_member_id_from_napcat(str(group_id), user_id_or_name)
                if user_id:
                    return user_id, str(group_id)

            # 尝试在好友列表中查找
            user_id = await self._get_friend_id_from_napcat(user_id_or_name)
            if user_id:
                return user_id, None

        # 4. 如果没有找到用户ID，尝试通过 LLM 提供的 group_id 查找
        if group_id and str(group_id).isdigit():
            if user_id_or_name and str(user_id_or_name).isdigit():
                return str(user_id_or_name), str(group_id)
        
        # 5. 如果仍然没有找到，从LLM响应文本中提取
        match_group = re.search(r'group_id:\s*(\d+)', self.llm_response_text)
        match_user = re.search(r'user_id:\s*(\d+)', self.llm_response_text)
        if match_group:
            group_id = match_group.group(1)
        if match_user:
            user_id = match_user.group(1)
            return user_id, group_id

        logger.warning(f"无法从任何可用来源获取到有效的 user_id 或 group_id。")
        return None, None


    async def execute(self) -> Tuple[bool, str]:
        user_id, group_id = await self.get_user_and_group_id()
        poke_mode = self.action_data.get("poke_mode", "被动")
        reply_id = self.action_data.get("reply_id")

        if POKE_DEBUG:
            logger.info(f"poke参数: user_id={user_id}, group_id={group_id}, poke_mode={poke_mode}")

        if not user_id:
            if POKE_DEBUG:
                await self.send_text("戳一戳失败，无法找到目标用户ID。")
                logger.warning(f"戳一戳失败，无法找到目标用户ID。")
            return False, "无法找到目标用户ID"

        # 检查是否重复戳了同一个人
        if self.last_poke_user == user_id and self.last_poke_group == group_id and (time.time() - self._last_poke_time < 300):
            logger.warning("避免重复戳同一个人")
            return False, "避免重复戳同一个人"

        if group_id:
            ok, result = self._send_group_poke(group_id, reply_id, user_id)
            self.last_poke_group = group_id
        else:
            ok, result = self._send_friend_poke(user_id)
            self.last_poke_group = None
            
        self.last_poke_user = user_id
        self._last_poke_time = time.time()

        if ok:
            reason = self.action_data.get("reason", self.reasoning or "无")
            await database_api.store_action_info(
                chat_stream=self.chat_stream,  # 从BaseAction继承来的
                action_build_into_prompt=True,
                action_prompt_display=f"使用了戳一戳，原因：{reason}",
                action_done=True,
                action_data={"reason": reason},  # 只存原因
                action_name="poke"              # 存名字
            )
            logger.info(f"存储成功")
            return True, "戳一戳成功"
        else:
            if POKE_DEBUG:
                await self.send_text(f"戳一戳失败: {result}")
            return False, f"戳一戳失败: {result}"

    def _send_group_poke(self, group_id: Optional[str], reply_id: Optional[int], user_id: str):
        if not group_id or not str(group_id).isdigit():
            logger.warning(f"[poke_plugin] 无效的 group_id={group_id}，尝试使用默认群号。")
            return False, "无效的群ID"

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
    plugin_version = "0.4.2"
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
            "version": ConfigField(str, default="0.4.2", description="插件版本"),
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