from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, List, Optional, Tuple, Type

from src.plugin_system import (
    ActionActivationType,
    BaseAction,
    BasePlugin,
    ConfigField,
    database_api,
    get_logger,
    person_api,
    register_plugin,
)

logger = get_logger("acpoke_plugin")

if TYPE_CHECKING:
    from src.plugin_system import ComponentInfo


class PokeAction(BaseAction):
    """QQ 戳一戳 Action（兼容新版去除 llm_judge 的插件系统）。"""

    action_name = "poke"
    action_description = "调用 QQ 戳一戳功能"

    # 新版插件系统仅支持 NEVER/ALWAYS/RANDOM/KEYWORD；这里保持 ALWAYS，让模型在合适场景下可选择。
    # 如需强制仅关键词触发，可在下方新增 KEYWORD Action（保持开闭原则）。
    activation_type = ActionActivationType.ALWAYS

    # 关键词仅作为显式触发信号提供给模型/激活器（不同版本实现可能不同，保留不耦合旧 llm_judge）。
    activation_keywords = ["戳我", "戳一下", "poke"]
    keyword_case_sensitive = False

    associated_types = ["text"]
    parallel_action = False

    action_parameters = {
        "user_id": "要戳的用户名称或 QQ 号；常见值如“我/自己/昵称/123456”。",
        "group_id": "群 ID（可选，不填会自动从上下文推断）",
        "reply_id": "回复消息 ID（可选）",
        "poke_mode": "主动或被动（可选，仅用于记录）",
    }

    action_require = [
        "用户明确要求“戳我/戳一下/poke”时可以使用",
        "友好互动氛围时可偶尔使用，但不要频繁",
        "不要在短时间内连续戳同一个人（尤其是群聊）",
    ]

    last_poke_user: Optional[str] = None
    last_poke_group: Optional[str] = None
    _last_poke_time: float = 0.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning: Optional[str] = kwargs.get("reasoning")
        self.llm_response_text: str = kwargs.get("llm_response_text", "") or ""

    # ------------------------------------------------------
    # ✔ 删除 Napcat 请求相关函数，并保留人物和群查找逻辑
    #    但不再使用 Napcat，而使用 SEND_POKE
    # ------------------------------------------------------

    def _infer_group_id_from_context(self) -> Optional[str]:
        group_id = self.action_data.get("group_id")
        if group_id in (None, "", "None"):
            group_id = None

        if not group_id and hasattr(self, "message") and getattr(self.message, "message_info", None):
            group_id = getattr(self.message.message_info, "group_id", None)
        if not group_id and hasattr(self, "chat_stream") and getattr(self.chat_stream, "group_id", None):
            group_id = self.chat_stream.group_id
        if not group_id and hasattr(self, "group_id"):
            group_id = getattr(self, "group_id", None)

        return str(group_id) if group_id not in (None, "", "None") else None

    async def get_user_and_group_id(self) -> Tuple[Optional[str], Optional[str]]:
        user_id_or_name = (self.action_data.get("user_id") or "").strip()
        group_id = self._infer_group_id_from_context()

        if user_id_or_name in {"我", "我自己", "自己", "me"} and getattr(self, "user_id", None):
            return str(getattr(self, "user_id")), group_id

        # 直接填写纯数字 QQ 号
        if user_id_or_name and user_id_or_name.isdigit():
            return user_id_or_name, group_id

        # 优先通过 person_api（昵称/名称 -> user_id）
        if user_id_or_name:
            try:
                person_id = person_api.get_person_id_by_name(user_id_or_name)
                if person_id:
                    uid = await person_api.get_person_value(person_id, "user_id")
                    if uid:
                        return str(uid), group_id
            except Exception as e:
                logger.error(f"person_api 查找出错: {e}")

        # 兼容：从 LLM 返回文本中解析 user_id/group_id
        match_group = re.search(r"group_id\\s*:\\s*(\\d+)", self.llm_response_text)
        match_user = re.search(r"user_id\\s*:\\s*(\\d+)", self.llm_response_text)
        if match_group:
            group_id = match_group.group(1)
        if match_user:
            return match_user.group(1), group_id

        return None, group_id

    # ------------------------------------------------------
    # ✔ 最终替换发送方式为 send_command("SEND_POKE")
    # ------------------------------------------------------

    def _build_send_poke_args(self, user_id: str, group_id: Optional[str]) -> List[dict]:
        """
        adapter 的 SEND_POKE 参数结构在不同版本可能不同。
        这里提供多组候选参数，逐个尝试以兼容更多适配器实现。
        """
        candidates: List[dict] = []

        args1: dict = {"qq_id": user_id}
        if group_id:
            args1["group_id"] = group_id
        candidates.append(args1)

        args2: dict = {"target_id": user_id}
        if group_id:
            args2["group_id"] = group_id
        candidates.append(args2)

        return candidates

    async def _send_poke(self, user_id: str, group_id: Optional[str]) -> Tuple[bool, str]:
        command_name = self.get_config("poke.command_name", "SEND_POKE")
        target_user_name = self.action_data.get("user_id") or user_id

        for args in self._build_send_poke_args(user_id=user_id, group_id=group_id):
            try:
                ok = await self.send_command(
                    command_name,
                    args,
                    display_message=f"[戳了戳 {target_user_name}]",
                )
                if ok:
                    return True, "SEND_POKE 已发送"
            except Exception as e:
                logger.warning(f"SEND_POKE 发送异常，参数={args}: {e}")

        return False, f"{command_name} 发送失败"

    # ------------------------------------------------------

    async def execute(self) -> Tuple[bool, str]:
        user_id, group_id = await self.get_user_and_group_id()
        poke_mode = self.action_data.get("poke_mode", "被动")

        if self.get_config("poke.debug", False):
            logger.info(f"poke参数: user_id={user_id}, group_id={group_id}, poke_mode={poke_mode}")

        if not user_id:
            return False, "无法找到目标用户ID"

        # 反复戳同一个人限制
        cooldown_seconds = int(self.get_config("poke.cooldown_seconds", 300))
        if (
            self.last_poke_user == user_id
            and self.last_poke_group == group_id
            and time.time() - self._last_poke_time < cooldown_seconds
        ):
            return False, "避免重复戳同一个人"

        ok, result = await self._send_poke(user_id=user_id, group_id=group_id)
        self.last_poke_group = group_id if group_id else None

        self.last_poke_user = user_id
        self._last_poke_time = time.time()

        if ok:
            reason = self.action_data.get("reason", self.reasoning or "无")
            await database_api.store_action_info(
                chat_stream=self.chat_stream,
                action_build_into_prompt=True,
                action_prompt_display=f"使用了戳一戳，原因：{reason}",
                action_done=True,
                action_data={"reason": reason},
                action_name="poke"
            )
            return True, "戳一戳成功"
        else:
            if self.get_config("poke.debug", False):
                await self.send_text(f"戳一戳失败: {result}")
            return False, f"戳一戳失败: {result}"


@register_plugin
class PokePlugin(BasePlugin):
    """戳一戳插件。"""

    plugin_name: str = "acpoke_plugin"
    plugin_description = "QQ戳一戳插件：支持主动、被动、戳回去功能"
    plugin_version = "0.5.1"
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
            "name": ConfigField(type=str, default="acpoke_plugin", description="插件名称"),
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "version": ConfigField(type=str, default="0.5.1", description="插件版本"),
            "description": ConfigField(type=str, default="QQ戳一戳插件", description="插件描述"),
        },
        "poke": {
            "command_name": ConfigField(type=str, default="SEND_POKE", description="Adapter命令名（一般无需修改）"),
            "cooldown_seconds": ConfigField(type=int, default=300, description="同一目标冷却时间（秒）"),
            "debug": ConfigField(type=bool, default=False, description="是否开启调试日志"),
        },
    }


    def get_plugin_components(self) -> List[Tuple[ComponentInfo, Type]]:
        return [
            (PokeAction.get_action_info(), PokeAction),
        ]
