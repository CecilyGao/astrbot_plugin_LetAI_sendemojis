from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.config.astrbot_config import AstrBotConfig
import json
import os
import random
import aiohttp
import asyncio
import re
import time

@register("letai_sendemojis", "Heyh520", "让AI智能发送表情包的AstrBot插件", "1.0.0")
class LetAISendEmojisPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        
        # 加载配置文件
        self.config = config
        
        # 初始化配置参数
        self.enable_context_parsing = self.config.get("enable_context_parsing", True)
        self.send_probability = self.config.get("send_probability", 0.3)
        self.request_timeout = self.config.get("request_timeout", 15)
        
        # 读取黑名单配置
        self.group_blacklist = self.config.get("group_blacklist", [])
        self.user_blacklist = self.config.get("user_blacklist", [])
        
        # 智能解析表情包数据源
        emoji_source = self.config.get("emoji_source", "").strip()
        self.emoji_source = emoji_source if emoji_source else "https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/chinesebqb_github.json"
        
        # 插件工作目录（固定在插件目录下）
        self.plugin_dir = os.path.dirname(__file__)
        self.emoji_directory = os.path.join(self.plugin_dir, "emojis")
        
        # 初始化表情包数据
        self.emoji_data = []
        
        # 添加表情包使用历史记录，避免短期重复
        self.recent_used_emojis = []  # 存储最近使用的表情包
        self.max_recent_history = 10  # 最多记录最近10个使用的表情包
        
        # 上下文情感记忆系统
        self.conversation_context = []  # 存储对话上下文
        self.max_context_length = 5  # 记住最近5轮对话
        self.current_ai_mood = "neutral"  # AI当前情绪状态
        self.mood_consistency_factor = 0.7  # 情绪一致性系数
        
        logger.info(f"LetAI表情包插件初始化完成 - 配置: enable_context_parsing={self.enable_context_parsing}, send_probability={self.send_probability}")
        logger.info(f"表情包数据源: {self.emoji_source}")
        logger.info(f"表情包工作目录: {self.emoji_directory}")
        logger.info(f"群聊黑名单: {len(self.group_blacklist)}个, 用户黑名单: {len(self.user_blacklist)}个")

    # 黑名单检查方法
    def _check_blacklist(self, event: AstrMessageEvent) -> bool:
        """检查当前群和用户是否在黑名单中"""
        # 检查群聊黑名单
        group_id = str(event.get_group_id())
        if group_id and group_id in self.group_blacklist:
            logger.debug(f"群 {group_id} 在黑名单中，跳过处理")
            return False
        
        # 检查用户黑名单
        user_id = str(event.get_sender_id())
        if user_id and user_id in self.user_blacklist:
            logger.debug(f"用户 {user_id} 在黑名单中，跳过处理")
            return False
        
        return True

    async def initialize(self):
        """插件初始化方法，加载表情包数据"""
        await self.load_emoji_data()
        logger.info(f"LetAI表情包插件已初始化，表情包数量: {len(self.emoji_data)}")
    
    async def terminate(self):
        """插件销毁方法"""
        logger.info("LetAI表情包插件已停止")
    
    async def load_emoji_data(self):
        """智能加载表情包数据，支持多种数据源"""
        logger.info("开始加载表情包数据...")
        
        # 确保工作目录存在
        os.makedirs(self.emoji_directory, exist_ok=True)
        
        # 智能判断数据源类型并加载
        source_type = self.detect_source_type(self.emoji_source)
        logger.info(f"检测到数据源类型: {source_type}")
        
        if source_type == "cached":
            # 优先使用缓存
            if await self.load_from_cache():
                logger.info(f"从缓存加载完成，共 {len(self.emoji_data)} 个表情包")
                return
        
        if source_type == "url":
            await self.load_from_url()
        elif source_type == "json_file":
            await self.load_from_json_file()
        elif source_type == "directory":
            await self.load_from_directory()
        else:
            logger.error(f"不支持的数据源类型: {self.emoji_source}")
            self.emoji_data = []
        
        logger.info(f"表情包数据加载完成，共 {len(self.emoji_data)} 个表情包")
    
    def detect_source_type(self, source):
        """智能检测数据源类型"""
        if not source:
            return "cached"  # 空配置优先使用缓存
            
        if source.startswith(("http://", "https://")):
            return "url"
        elif source.endswith(".json") and os.path.isfile(source):
            return "json_file"
        elif os.path.isdir(source):
            return "directory"
        else:
            # 检查是否有缓存
            cache_file = os.path.join(self.emoji_directory, "emoji_cache.json")
            if os.path.exists(cache_file):
                return "cached"
            else:
                return "url"  # 默认当作URL处理
    
    
    async def load_from_cache(self):
        """从缓存加载"""
        try:
            cache_file = os.path.join(self.emoji_directory, "emoji_cache.json")
            if not os.path.exists(cache_file):
                return False
                
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # 处理新的缓存格式 {"data": [...], "cache_info": {...}} 或旧格式 [...]
            emoji_list = []
            if isinstance(data, dict) and "data" in data:
                # 新格式：包含完整信息的缓存
                emoji_list = data["data"]
                cache_info = data.get("cache_info", {})
                logger.info(f"加载缓存信息: 总计{cache_info.get('total_count', 0)}个表情包")
            elif isinstance(data, list):
                # 旧格式：直接是表情包数组
                emoji_list = data
            
            if len(emoji_list) > 0:
                # 更新local_path以确保一致性
                for emoji in emoji_list:
                    if "local_path" not in emoji:
                        emoji["local_path"] = self.generate_local_path(emoji)
                
                # 验证本地文件是否存在的表情包
                valid_emojis = []
                for emoji in emoji_list:
                    local_path = emoji.get("local_path")
                    if local_path and os.path.exists(local_path):
                        valid_emojis.append(emoji)
                
                # 加载所有数据（包括未下载的），但统计本地可用数量
                self.emoji_data = emoji_list
                logger.info(f"从缓存加载了 {len(emoji_list)} 个表情包，其中 {len(valid_emojis)} 个本地可用")
                return True
            return False
        except Exception as e:
            logger.warning(f"加载缓存失败: {e}")
            return False
    
    async def load_from_url(self):
        """从网络URL加载JSON数据"""
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=10,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
            logger.info(f"正在请求: {self.emoji_source}")
            
            try:
                async with session.get(self.emoji_source) as response:
                    if response.status == 200:
                        response_text = await response.text()
                        json_data = json.loads(response_text)
                        
                        if isinstance(json_data, dict) and "data" in json_data:
                            emoji_list = json_data["data"]
                        elif isinstance(json_data, list):
                            emoji_list = json_data
                        else:
                            logger.error("不支持的JSON格式")
                            return
                        
                        self.emoji_data = []
                        for emoji in emoji_list:
                            # 保留原始JSON的所有字段
                            emoji_item = emoji.copy()
                            
                            # 确保使用原始GitHub地址
                            original_url = emoji_item.get("url", "")
                            if original_url and not original_url.startswith("http"):
                                emoji_item["url"] = f"https://raw.githubusercontent.com/zhaoolee/ChineseBQB/master/{original_url.lstrip('./')}"
                            
                            # 添加本地路径字段（额外信息，不替换原有信息）
                            emoji_item["local_path"] = self.generate_local_path(emoji)
                            
                            self.emoji_data.append(emoji_item)
                        
                        logger.info(f"成功加载了 {len(self.emoji_data)} 个表情包")
                        
                        await self.save_cache()
                        # 不再预先批量下载，改为按需下载
                        logger.info("表情包数据已加载，将采用按需下载模式")
                        
                    else:
                        logger.error(f"HTTP响应错误: {response.status}")
                        
            except Exception as e:
                logger.error(f"网络请求失败: {e}")
                logger.info("尝试使用缓存数据...")
                if await self.load_from_cache():
                    logger.info("成功使用缓存数据")
                else:
                    logger.warning("无可用的表情包数据")
    
    async def load_from_json_file(self):
        """从本地JSON文件加载"""
        try:
            with open(self.emoji_source, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            
            # 处理不同JSON格式
            if isinstance(json_data, dict) and "data" in json_data:
                emoji_list = json_data["data"]
            elif isinstance(json_data, list):
                emoji_list = json_data
            else:
                logger.error("不支持的JSON格式")
                return
            
            self.emoji_data = []
            for emoji in emoji_list:
                # 保留原始JSON的所有字段
                emoji_item = emoji.copy()
                
                # 如果没有local_path则生成（额外添加，不替换原有信息）
                if "local_path" not in emoji_item:
                    emoji_item["local_path"] = self.generate_local_path(emoji)
                    
                self.emoji_data.append(emoji_item)
            
            logger.info(f"从JSON文件加载了 {len(self.emoji_data)} 个表情包")
            
        except Exception as e:
            logger.error(f"从JSON文件加载失败: {e}")
    
    async def load_from_directory(self):
        """从本地目录扫描表情包文件"""
        try:
            emoji_files = []
            supported_formats = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
            
            for root, dirs, files in os.walk(self.emoji_source):
                for file in files:
                    if any(file.lower().endswith(fmt) for fmt in supported_formats):
                        file_path = os.path.join(root, file)
                        relative_path = os.path.relpath(file_path, self.emoji_source)
                        
                        # 从目录结构推断分类
                        category = os.path.dirname(relative_path) if os.path.dirname(relative_path) else "其他"
                        
                        emoji_files.append({
                            "name": file,
                            "category": category,
                            "url": f"file://{file_path}",
                            "local_path": file_path
                        })
            
            self.emoji_data = emoji_files
            logger.info(f"从目录扫描了 {len(self.emoji_data)} 个表情包文件")
            
        except Exception as e:
            logger.error(f"从目录加载失败: {e}")
    
    def generate_local_path(self, emoji):
        name = emoji.get("name", "")
        category = emoji.get("category", "其他")
        
        if not name:
            return ""
            
        category_dir = os.path.join(self.emoji_directory, category)
        return os.path.join(category_dir, name)
    
    
    async def save_cache(self):
        """保存缓存，格式仿造ChineseBQB的JSON结构"""
        try:
            cache_file = os.path.join(self.emoji_directory, "emoji_cache.json")
            
            # 创建仿造ChineseBQB格式的缓存数据
            cache_data = {
                "data": self.emoji_data,
                "cache_info": {
                    "total_count": len(self.emoji_data),
                    "local_available": sum(1 for emoji in self.emoji_data 
                                         if emoji.get("local_path") and os.path.exists(emoji.get("local_path", ""))),
                    "last_updated": json.dumps({"timestamp": "auto-generated"}, ensure_ascii=False),
                    "source": "AstrBot LetAI SendEmojis Plugin"
                }
            }
            
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, ensure_ascii=False, indent=2)
                
            logger.info(f"缓存已保存: {cache_file} (包含完整的表情包信息)")
            logger.info(f"缓存统计: 总计{cache_data['cache_info']['total_count']}个, 本地可用{cache_data['cache_info']['local_available']}个")
            
        except Exception as e:
            logger.warning(f"保存缓存失败: {e}")
    
    # 已移除批量下载逻辑，改为按需下载模式
    
    @filter.command("测试表情包下载", "test_emoji_download")
    async def test_download_command(self, event: AstrMessageEvent):
        """测试表情包下载功能"""
        if not self.emoji_data:
            return event.text_result("表情包数据为空")
        
        # 随机选择一个表情包进行测试
        import random
        test_emoji = random.choice(self.emoji_data)
        
        logger.info(f"开始测试下载: {test_emoji.get('name')}")
        success = await self.download_single_emoji(test_emoji)
        
        if success:
            return event.text_result(f"✅ 下载测试成功: {test_emoji.get('name')}")
        else:
            return event.text_result(f"❌ 下载测试失败: {test_emoji.get('name')}")
    
    @filter.command("查看缓存信息", "check_cache_info")
    async def check_cache_info(self, event: AstrMessageEvent):
        """查看表情包缓存信息"""
        cache_file = os.path.join(self.emoji_directory, "emoji_cache.json")
        
        if not os.path.exists(cache_file):
            return event.text_result("❌ 缓存文件不存在")
        
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if isinstance(data, dict) and "cache_info" in data:
                cache_info = data["cache_info"]
                total = cache_info.get("total_count", 0)
                local = cache_info.get("local_available", 0)
                source = cache_info.get("source", "未知")
                
                info_text = f"""表情包缓存信息:
                
总计: {total} 个表情包
本地可用: {local} 个
下载率: {(local/total*100):.1f}% 
数据源: {source}
缓存文件: emoji_cache.json

插件采用按需下载模式：
- 优先使用本地已下载的表情包
- 找不到合适的时，从数据源搜索二次元表情包并立即下载
- 按分类自动存储到本地目录
- 逐步建立精准的本地表情包库"""
                
                return event.text_result(info_text)
            else:
                return event.text_result("⚠️ 旧格式缓存文件，建议重新加载插件更新格式")
                
        except Exception as e:
            return event.text_result(f"❌ 读取缓存失败: {e}")
    
    @filter.command("清理本地表情包", "clear_local_emojis")
    async def clear_local_emojis_command(self, event: AstrMessageEvent):
        """清理本地下载的表情包文件"""
        try:
            import shutil
            
            if os.path.exists(self.emoji_directory):
                # 统计删除的文件数量
                file_count = 0
                for root, dirs, files in os.walk(self.emoji_directory):
                    file_count += len([f for f in files if f.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp'))])
                
                # 删除整个表情包目录
                shutil.rmtree(self.emoji_directory)
                logger.info(f"已清理本地表情包目录: {self.emoji_directory}")
                
                return event.text_result(f"✅ 已清理 {file_count} 个本地表情包文件\n\n📥 下次AI发送表情包时将重新按需下载")
            else:
                return event.text_result("💭 本地表情包目录不存在，无需清理")
                
        except Exception as e:
            logger.error(f"清理本地表情包失败: {e}")
            return event.text_result(f"❌ 清理失败: {e}")
    
    @filter.command("查看使用历史", "check_usage_history")
    async def check_usage_history(self, event: AstrMessageEvent):
        """查看表情包使用历史"""
        if not self.recent_used_emojis:
            return event.text_result("表情包使用历史为空")
        
        history_text = "最近使用的表情包:\n\n"
        for i, emoji_id in enumerate(self.recent_used_emojis, 1):
            history_text += f"{i}. {emoji_id}\n"
        
        history_text += f"\n当前记录 {len(self.recent_used_emojis)}/{self.max_recent_history} 个，避免短期重复使用"
        
        return event.text_result(history_text)
    
    @filter.command("清空使用历史", "clear_usage_history")
    async def clear_usage_history(self, event: AstrMessageEvent):
        """清空表情包使用历史"""
        history_count = len(self.recent_used_emojis)
        self.recent_used_emojis.clear()
        logger.info("已清空表情包使用历史")
        return event.text_result(f"✅ 已清空 {history_count} 条使用历史记录\n\n🔄 现在可以重新使用之前的表情包了")
    
    @filter.command("表情包统计", "emoji_stats")
    async def emoji_stats(self, event: AstrMessageEvent):
        """查看表情包统计信息"""
        if not self.emoji_data:
            return event.text_result("❌ 表情包数据为空")
        
        total_count = len(self.emoji_data)
        downloaded_count = 0
        anime_count = 0
        
        anime_categories = self.get_anime_categories()
        
        for emoji in self.emoji_data:
            local_path = emoji.get("local_path")
            if local_path and os.path.exists(local_path):
                downloaded_count += 1
            
            emoji_name = emoji.get("name", "").lower()
            emoji_category = emoji.get("category", "").lower()
            is_anime = any(anime_key.lower() in emoji_category or 
                          anime_key.lower() in emoji_name for anime_key in anime_categories)
            if is_anime:
                anime_count += 1
        
        stats_text = f"""表情包统计信息:

总表情包数量: {total_count}
已下载到本地: {downloaded_count}
二次元表情包: {anime_count}
使用历史记录: {len(self.recent_used_emojis)}/{self.max_recent_history}

下载率: {(downloaded_count/total_count*100):.1f}%
二次元占比: {(anime_count/total_count*100):.1f}%
可下载数量: {total_count - downloaded_count}

策略说明:
- 30% 概率强制下载新表情包
- 本地不足5个时强制下载
- 优先选择未使用过的表情包"""
        
        return event.text_result(stats_text)
    
    @filter.command("查看AI情感状态", "check_ai_mood")
    async def check_ai_mood(self, event: AstrMessageEvent):
        """查看AI当前的情感状态和对话上下文"""
        mood_text = f"""AI情感状态报告:
        
当前AI情绪: {self.current_ai_mood}
情绪一致性系数: {self.mood_consistency_factor}
对话上下文长度: {len(self.conversation_context)}/{self.max_context_length}

最近对话记录:"""
        
        if self.conversation_context:
            for i, ctx in enumerate(self.conversation_context[-3:], 1):  # 显示最近3条
                time_str = time.strftime("%H:%M:%S", time.localtime(ctx["timestamp"]))
                mood_text += f"""
{i}. [{time_str}] 用户:{ctx['user_emotion']} → AI:{ctx['ai_emotion']}
   回复: {ctx['ai_reply_sample']}"""
        else:
            mood_text += "\n   暂无对话记录"
        
        mood_text += f"""

🎯 发送概率: {self.send_probability}
📈 智能调节: 根据情感强度、对话长度、时间间隔等因素动态调整

💡 AI情感特点:
- 保持70%情绪连贯性，避免情感跳跃过大
- 高情感强度时增加表情包发送概率
- 短时间内避免重复发送
- 根据用户情感进行智能响应"""
        
        return event.text_result(mood_text)
    
    @filter.command("重置AI情感", "reset_ai_mood")
    async def reset_ai_mood(self, event: AstrMessageEvent):
        """重置AI的情感状态和对话上下文"""
        old_mood = self.current_ai_mood
        old_context_len = len(self.conversation_context)
        
        self.current_ai_mood = "neutral"
        self.conversation_context.clear()
        
        logger.info("AI情感状态已重置")
        return event.text_result(f"""🔄 AI情感状态重置完成:

📊 重置前状态:
   - AI情绪: {old_mood}
   - 对话上下文: {old_context_len}条记录

📊 重置后状态:
   - AI情绪: {self.current_ai_mood}
   - 对话上下文: 已清空

🎭 AI现在将以全新的中性情绪开始对话""")
    
    @filter.command("调整情感一致性", "adjust_mood_consistency")
    async def adjust_mood_consistency(self, event: AstrMessageEvent):
        """调整AI情感一致性系数"""
        args = event.get_message().get_plain_text().split()
        if len(args) < 2:
            return event.text_result(f"""💡 当前情感一致性系数: {self.mood_consistency_factor}

🔧 使用方法: 调整情感一致性 <数值>
   数值范围: 0.1-1.0
   - 0.1: 情感变化很快，更随性
   - 0.5: 平衡状态
   - 1.0: 情感非常稳定，很少变化

示例: 调整情感一致性 0.8""")
        
        try:
            new_factor = float(args[1])
            if 0.1 <= new_factor <= 1.0:
                old_factor = self.mood_consistency_factor
                self.mood_consistency_factor = new_factor
                logger.info(f"情感一致性系数调整: {old_factor} -> {new_factor}")
                return event.text_result(f"""✅ 情感一致性系数调整成功:

📊 调整详情:
   - 原数值: {old_factor}
   - 新数值: {new_factor}

🎭 效果说明:
   {'AI情感会更加稳定，较少出现突然的情感变化' if new_factor > 0.7 else 'AI情感会更加活跃，容易根据对话内容变化' if new_factor < 0.5 else 'AI情感保持平衡状态'}""")
            else:
                return event.text_result("❌ 数值超出范围，请输入0.1-1.0之间的数值")
        except ValueError:
            return event.text_result("❌ 请输入有效的数字")
    
    async def download_single_emoji(self, emoji):
        """立即下载单个表情包"""
        local_path = emoji.get("local_path")
        url = emoji.get("url")
        
        if not local_path or not url:
            return False
        
        if os.path.exists(local_path):
            return True
        
        # 创建目录
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        
        timeout = aiohttp.ClientTimeout(total=15)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        
        connector = aiohttp.TCPConnector(
            ssl=False,
            limit=1,
            ttl_dns_cache=300,
            use_dns_cache=True,
        )
        
        try:
            logger.info(f"下载表情包: {emoji.get('name')} <- {url}")
            
            async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        with open(local_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)
                        logger.info(f"下载成功: {emoji.get('name')}")
                        return True
                    else:
                        logger.warning(f"HTTP错误 {response.status}: {emoji.get('name')}")
                        return False
                        
        except Exception as e:
            logger.warning(f"下载失败: {emoji.get('name')} - {e}")
            return False
    
    
    
    @filter.on_decorating_result()
    async def on_ai_reply(self, event: AstrMessageEvent):
        # 检查黑名单
        if not self._check_blacklist(event):
            return
            
        if not self.enable_context_parsing or not self.emoji_data:
            return
            
        result = event.get_result()
        if not result or not result.chain:
            return
            
        ai_reply_text = ""
        for message_component in result.chain:
            if hasattr(message_component, 'text'):
                ai_reply_text += message_component.text
        
        if not ai_reply_text.strip():
            return
            
        # 分析用户和AI的情感，并更新上下文
        # 获取用户消息
        user_message = event.get_message_str() if hasattr(event, 'get_message_str') else (event.message_str if hasattr(event, 'message_str') else "")
        
        user_emotion = self.analyze_user_emotion(user_message)
        ai_emotion = self.analyze_ai_reply_emotion(ai_reply_text)
        
        # 更新对话上下文和AI情绪状态
        self.update_conversation_context(user_emotion, ai_emotion, ai_reply_text)
        
        # 智能决定是否发送表情包（基于情感强度和上下文）
        should_send_emoji = self.should_send_emoji_intelligent(user_emotion, ai_emotion, ai_reply_text)
        
        if should_send_emoji:
            selected_emoji = await self.search_emoji_by_emotion(ai_emotion, ai_reply_text)
            
            if selected_emoji:
                logger.info(f"将单独发送表情包: {selected_emoji.get('name', '未知')}")
                
                # 异步发送表情包，不阻塞主消息
                asyncio.create_task(self.send_emoji_separately(event, selected_emoji))
    
    async def send_emoji_separately(self, event: AstrMessageEvent, selected_emoji):
        """单独发送表情包"""
        try:
            local_path = selected_emoji.get("local_path")
            
            # 检查本地文件是否存在（搜索时应该已经确保下载了）
            if local_path and os.path.exists(local_path):
                logger.info(f"发送二次元表情包: {selected_emoji.get('name')}")
                # 使用正确的消息链API发送图片
                message_chain = MessageChain([Image(file=local_path)])
                await event.send(message_chain)
                logger.info(f"表情包发送成功: {selected_emoji.get('name')}")
            else:
                # 如果搜索方法返回了表情包但本地文件不存在，说明有问题
                logger.error(f"表情包本地文件不存在: {selected_emoji.get('name')} - {local_path}")
                logger.warning("跳过表情包发送")
                    
        except Exception as e:
            logger.error(f"发送表情包失败: {selected_emoji.get('name')} - {e}")
    
    def analyze_ai_reply_emotion(self, ai_reply: str):
        """深度分析AI回复的情感和内容，返回精准的情感标签"""
        reply_lower = ai_reply.lower()
        
        # 更精准的情感分析模式 - 基于语义而非单纯关键词
        emotion_patterns = {
            # 积极情感
            "happy_excited": {
                "keywords": ["哈哈", "开心", "高兴", "快乐", "太好了", "棒", "赞", "笑", "嘻嘻", "太棒了", "amazing", "wow", "激动", "兴奋", "厉害", "牛逼", "绝了"],
                "weight": 2.0
            },
            "friendly_warm": {
                "keywords": ["你好", "欢迎", "很高兴", "谢谢", "不客气", "希望", "祝", "关心", "温暖", "陪伴"],
                "weight": 1.5
            },
            "cute_playful": {
                "keywords": ["可爱", "萌", "么么", "mua", "小可爱", "乖", "软萌", "调皮", "淘气", "嘿嘿", "逗", "搞怪", "～", "~", "嘿嘿", "啦", "呀", "哟"],
                "weight": 2.0
            },
            
            # 关怀情感
            "caring_gentle": {
                "keywords": ["要注意", "小心", "多休息", "保重", "记得", "别忘了", "照顾", "温柔", "慢慢", "不要着急", "别担心", "没关系"],
                "weight": 1.8
            },
            
            # 认知情感
            "thinking_wise": {
                "keywords": ["我觉得", "分析", "考虑", "思考", "建议", "或许", "可能", "应该", "经验", "学习", "明白", "理解"],
                "weight": 1.2
            },
            
            # 惊讶好奇
            "surprised_curious": {
                "keywords": ["哇", "真的吗", "没想到", "惊讶", "意外", "竟然", "原来", "好奇", "想知道", "有趣", "为什么", "怎么", "探索"],
                "weight": 1.6
            },
            
            # 鼓励支持
            "encouraging": {
                "keywords": ["相信", "能行", "加油", "努力", "坚持", "不放弃", "一定可以", "支持"],
                "weight": 1.5
            },
            
            # 特定主题
            "food_related": {
                "keywords": ["吃", "美食", "饿", "香", "好吃", "味道", "料理", "烹饪", "餐厅", "菜", "饭"],
                "weight": 2.5
            },
            "sleep_tired": {
                "keywords": ["睡", "困", "休息", "累", "梦", "床", "被子", "打哈欠"],
                "weight": 2.5
            },
            "work_study": {
                "keywords": ["工作", "学习", "任务", "完成", "专注", "效率", "上班", "考试", "作业"],
                "weight": 2.0
            },
            "gaming": {
                "keywords": ["游戏", "玩", "通关", "技能", "战斗", "冒险", "娱乐", "开黑", "上分"],
                "weight": 2.5
            },
            
            # 道歉谦虚
            "apologetic": {
                "keywords": ["对不起", "抱歉", "不好意思", "sorry", "打扰", "麻烦", "我还在学习", "可能不够", "尽力"],
                "weight": 1.8
            },
            
            # 困惑
            "confused": {
                "keywords": ["不太明白", "疑惑", "困惑", "不确定", "可能需要", "不知道", "搞不懂"],
                "weight": 1.5
            },
            
            # 感谢
            "grateful": {
                "keywords": ["感谢", "谢谢", "感激", "感恩", "appreciate", "thanks"],
                "weight": 1.5
            }
        }
        
        # 计算情感分数，考虑权重
        emotion_scores = {}
        for emotion, config in emotion_patterns.items():
            keywords = config["keywords"]
            weight = config["weight"]
            
            # 计算匹配分数
            matches = sum(1 for keyword in keywords if keyword in reply_lower)
            if matches > 0:
                # 考虑匹配数量、权重和文本长度
                base_score = matches * weight
                length_factor = min(1.5, len(ai_reply) / 50)  # 较短文本权重更高
                emotion_scores[emotion] = base_score * length_factor
        
        # 返回得分最高的情感，增加一些随机性避免过于固定
        if emotion_scores:
            # 获取前几名的情感，增加选择的多样性
            sorted_emotions = sorted(emotion_scores.items(), key=lambda x: x[1], reverse=True)
            
            # 如果有多个得分相近的情感，随机选择一个
            if len(sorted_emotions) >= 2:
                top_score = sorted_emotions[0][1]
                # 找出得分在top_score的80%以上的情感
                threshold = top_score * 0.8
                top_candidates = [emotion for emotion, score in sorted_emotions if score >= threshold]
                
                if len(top_candidates) > 1:
                    selected_emotion = random.choice(top_candidates)
                    logger.info(f"AI情感分析结果(多候选): {selected_emotion} (分数: {emotion_scores[selected_emotion]:.2f})")
                    return selected_emotion
            
            # 默认返回最高分
            top_emotion = sorted_emotions[0][0]
            logger.info(f"AI情感分析结果: {top_emotion} (分数: {emotion_scores[top_emotion]:.2f})")
            return top_emotion
        else:
            # 随机返回一些基础情感，避免总是"neutral"
            fallback_emotions = ["friendly_warm", "cute_playful", "happy_excited", "thinking_wise"]
            selected = random.choice(fallback_emotions)
            logger.info(f"AI情感分析: 未识别特定情感，随机使用: {selected}")
            return selected
    
    async def search_emoji_by_emotion(self, ai_emotion: str, ai_reply_text: str):
        """基于AI回复内容的主题精准搜索匹配的表情包（优先二次元，优先本地）"""
        if not self.emoji_data:
            return None
            
        anime_categories = self.get_anime_categories()
        
        # 基于AI回复内容主题的关键词映射
        emotion_mapping = {
            "happy_excited": {
                "primary": ["开心", "笑", "高兴", "快乐", "哈哈", "嘻嘻", "兴奋", "激动", "开森", "快乐", "爽", "太棒"],
                "secondary": ["好", "棒", "赞", "厉害", "牛", "爱了", "666"]
            },
            "friendly_warm": {
                "primary": ["友好", "亲切", "微笑", "温暖", "欢迎", "你好", "见面", "打招呼"],
                "secondary": ["好", "棒", "开心", "爱", "亲"]
            },
            "cute_playful": {
                "primary": ["可爱", "萌", "卖萌", "软萌", "调皮", "淘气", "搞怪", "玩耍", "嬉戏", "呆萌", "小可爱"],
                "secondary": ["逗", "乖", "小", "呆", "萌萌哒"]
            },
            "caring_gentle": {
                "primary": ["关心", "照顾", "温柔", "体贴", "爱护", "安慰", "抱抱", "保重", "小心"],
                "secondary": ["好", "乖", "温暖", "爱", "心疼"]
            },
            "thinking_wise": {
                "primary": ["思考", "想", "考虑", "琢磨", "智慧", "学习", "明白", "理解", "分析", "研究"],
                "secondary": ["疑问", "想想", "嗯", "思索"]
            },
            "surprised_curious": {
                "primary": ["惊讶", "哇", "震惊", "意外", "好奇", "有趣", "探索", "发现", "没想到", "真的"],
                "secondary": ["什么", "真的", "原来", "咦"]
            },
            "encouraging": {
                "primary": ["加油", "努力", "支持", "相信", "坚持", "能行", "鼓励", "加把劲"],
                "secondary": ["好", "棒", "厉害", "可以", "行"]
            },
            "food_related": {
                "primary": ["吃", "美食", "饿", "香", "馋", "好吃", "味道", "料理", "饭", "菜", "食物", "餐厅", "烹饪"],
                "secondary": ["口水", "流口水", "想吃", "香香", "饕餮"]
            },
            "sleep_tired": {
                "primary": ["睡", "困", "累", "休息", "梦", "床", "被子", "打哈欠", "疲惫", "瞌睡"],
                "secondary": ["想睡", "累了", "乏"]
            },
            "work_study": {
                "primary": ["工作", "学习", "任务", "完成", "专注", "效率", "上班", "考试", "作业", "忙碌"],
                "secondary": ["忙", "努力", "加班", "书", "学"]
            },
            "gaming": {
                "primary": ["游戏", "玩", "通关", "技能", "战斗", "冒险", "娱乐", "开黑", "上分", "电竞", "操作"],
                "secondary": ["打游戏", "玩游戏", "胜利", "输了", "菜"]
            },
            "apologetic": {
                "primary": ["对不起", "抱歉", "不好意思", "sorry", "道歉", "错了"],
                "secondary": ["错", "不对", "麻烦", "失误"]
            },
            "confused": {
                "primary": ["疑惑", "困惑", "不明白", "想想", "不知道", "搞不懂", "迷茫"],
                "secondary": ["什么", "为什么", "怎么", "咋办"]
            },
            "grateful": {
                "primary": ["感谢", "谢谢", "感激", "感恩", "thanks", "多谢"],
                "secondary": ["好", "棒", "爱了", "感动"]
            }
        }
        
        # 获取AI回复内容对应的关键词
        mapping = emotion_mapping.get(ai_emotion, {
            "primary": ["友好", "开心", "好"],
            "secondary": ["棒", "不错"]
        })
        
        primary_keywords = mapping["primary"]
        secondary_keywords = mapping["secondary"]
        
        # 增加多样性策略：有40%概率跳过本地搜索，直接在线下载新表情包（提高获取更多动漫表情包的机会）
        force_download = random.random() < 0.4
        
        if not force_download:
            # 第一步：在已下载的本地文件中搜索（优先二次元）
            local_matches = await self.search_local_emojis(primary_keywords, secondary_keywords, anime_categories)
            if local_matches:
                logger.info("使用本地表情包")
                return local_matches
        else:
            logger.info("强制多样性模式：跳过本地搜索，直接下载新表情包")
            
        # 第二步：在完整数据源中搜索二次元表情包，找到后立即下载
        return await self.search_and_download_anime_emoji(primary_keywords, secondary_keywords, anime_categories, ai_emotion)
    
    async def search_local_emojis(self, primary_keywords, secondary_keywords, anime_categories):
        """在本地已下载的表情包中搜索（优先二次元）"""
        local_perfect = []  # 本地二次元+主要关键词
        local_good = []     # 本地二次元+次要关键词
        local_anime = []    # 本地二次元表情包
        local_other = []    # 本地其他匹配
        
        for emoji in self.emoji_data:
            local_path = emoji.get("local_path")
            if not local_path or not os.path.exists(local_path):
                continue  # 只检查本地已存在的文件
                
            emoji_name = emoji.get("name", "").lower()
            emoji_category = emoji.get("category", "").lower()
            
            # 检查是否为二次元表情包（更智能的匹配算法）
            is_anime = self.is_anime_emoji(emoji_name, emoji_category, anime_categories)
            
            # 检查关键词匹配（更智能的匹配逻辑）
            search_text = f"{emoji_name} {emoji_category}".lower()
            
            # 主要关键词匹配
            primary_match = any(keyword in search_text for keyword in primary_keywords)
            
            # 次要关键词匹配
            secondary_match = any(keyword in search_text for keyword in secondary_keywords)
            
            # 从文件名中提取情感线索（文件名通常包含描述信息）
            name_emotions = self.extract_emotion_from_filename(emoji_name)
            emotion_enhanced_match = any(emotion in primary_keywords + secondary_keywords 
                                       for emotion in name_emotions)
            
            # 分类存储（优先二次元，二次元表情包有多重优先级）
            if is_anime and (primary_match or emotion_enhanced_match):
                # 二次元+完美匹配，添加多次增加权重
                local_perfect.extend([emoji] * 3)  # 增加3倍权重
            elif is_anime and secondary_match:
                # 二次元+良好匹配，添加2次增加权重
                local_good.extend([emoji] * 2)
            elif is_anime:
                # 纯二次元表情包，添加1.5倍权重
                local_anime.extend([emoji] * 2)
            elif primary_match or secondary_match or emotion_enhanced_match:
                local_other.append(emoji)
        
        # 按优先级返回本地表情包，并过滤最近使用过的
        all_local_candidates = local_perfect + local_good + local_anime + local_other
        
        # 如果本地可选表情包太少（少于8个），返回None强制在线下载（提高阈值，增加在线下载频率）
        if len(all_local_candidates) < 8:
            logger.info(f"本地表情包数量不足({len(all_local_candidates)}<8)，强制在线下载新表情包")
            return None
        
        selected = None
        selection_type = ""
        
        if local_perfect:
            # 过滤最近使用的表情包
            filtered_perfect = self.filter_recently_used(local_perfect)
            if filtered_perfect:  # 确保过滤后还有可选项
                selected = random.choice(filtered_perfect)
                selection_type = "本地完美匹配: 二次元+主题关键词"
        
        if not selected and local_good:
            filtered_good = self.filter_recently_used(local_good)
            if filtered_good:
                selected = random.choice(filtered_good)
                selection_type = "本地良好匹配: 二次元+相关关键词"
        
        if not selected and local_anime:
            filtered_anime = self.filter_recently_used(local_anime)
            if filtered_anime:
                selected = random.choice(filtered_anime)
                selection_type = "本地二次元表情包"
        
        if not selected and local_other:
            filtered_other = self.filter_recently_used(local_other)
            if filtered_other:
                selected = random.choice(filtered_other)
                selection_type = "本地其他匹配"
            
        if selected:
            # 添加到使用历史
            self.add_to_recent_used(selected)
            logger.info(f"{selection_type} - {selected.get('name')}")
            return selected
        else:
            # 本地表情包过滤后没有可选项，强制在线下载
            logger.info("本地表情包过滤后无可选项，强制在线下载新表情包")
            return None
    
    async def search_and_download_anime_emoji(self, primary_keywords, secondary_keywords, anime_categories, ai_emotion):
        """在完整数据源中搜索二次元表情包，找到后立即下载"""
        anime_perfect = []  # 二次元+主要关键词
        anime_good = []     # 二次元+次要关键词  
        anime_all = []      # 所有二次元表情包
        
        logger.info(f"开始搜索二次元表情包，总数据量: {len(self.emoji_data)}")
        
        # 只搜索二次元表情包，且排除已下载的
        checked_count = 0
        anime_count = 0
        for emoji in self.emoji_data:
            emoji_name = emoji.get("name", "").lower()
            emoji_category = emoji.get("category", "").lower()
            
            checked_count += 1
            
            # 检查是否为二次元表情包（更智能的匹配算法）
            is_anime = self.is_anime_emoji(emoji_name, emoji_category, anime_categories)
            
            if is_anime:
                anime_count += 1
            
            if not is_anime:
                continue  # 只处理二次元表情包
            
            # 排除已经下载到本地的表情包，优先下载新的
            local_path = emoji.get("local_path")
            if local_path and os.path.exists(local_path):
                continue  # 跳过已下载的，专注于下载新的
            
            # 检查关键词匹配
            search_text = f"{emoji_name} {emoji_category}".lower()
            primary_match = any(keyword in search_text for keyword in primary_keywords)
            secondary_match = any(keyword in search_text for keyword in secondary_keywords)
            
            # 从文件名中提取情感线索
            name_emotions = self.extract_emotion_from_filename(emoji_name)
            emotion_enhanced_match = any(emotion in primary_keywords + secondary_keywords 
                                       for emotion in name_emotions)
            
            # 分类存储（只保存二次元且未下载的）
            if primary_match or emotion_enhanced_match:
                anime_perfect.append(emoji)
            elif secondary_match:
                anime_good.append(emoji)
            else:
                anime_all.append(emoji)
        
        logger.info(f"表情包筛选结果: 总检查{checked_count}个, 识别为动漫{anime_count}个, 完美匹配{len(anime_perfect)}个, 良好匹配{len(anime_good)}个, 随机池{len(anime_all)}个")
        
        # 按优先级选择并下载表情包，过滤最近使用的
        candidates = []
        match_type = ""
        
        if anime_perfect:
            candidates = self.filter_recently_used(anime_perfect)
            match_type = f"完美匹配二次元+{ai_emotion}主题"
        elif anime_good:
            candidates = self.filter_recently_used(anime_good)
            match_type = f"良好匹配二次元+相关主题"
        elif anime_all:
            # 从所有二次元表情包中选择一部分，然后过滤最近使用的
            sample_size = min(50, len(anime_all))  # 进一步增加样本大小提高多样性
            sampled = random.sample(anime_all, sample_size)
            candidates = self.filter_recently_used(sampled)
            match_type = "随机二次元表情包"
        
        if candidates:
            selected = random.choice(candidates)
            logger.info(f"选中表情包: {match_type} - {selected.get('name')}")
            
            # 立即下载到本地并分类存储
            download_success = await self.download_single_emoji(selected)
            if download_success:
                # 添加到使用历史
                self.add_to_recent_used(selected)
                logger.info(f"按需下载成功: {selected.get('name')}")
                return selected
            else:
                logger.warning(f"按需下载失败: {selected.get('name')}")
                return None
        else:
            # 如果严格的动漫搜索没有结果，使用宽松的随机选择作为后备
            logger.warning("严格的二次元表情包搜索无结果，启用后备模式")
            return await self.fallback_emoji_selection()
    
    async def fallback_emoji_selection(self):
        """后备表情包选择方法：从所有表情包中随机选择"""
        if not self.emoji_data:
            return None
            
        # 获取所有未下载的表情包
        available_emojis = []
        for emoji in self.emoji_data:
            local_path = emoji.get("local_path")
            if not local_path or not os.path.exists(local_path):
                available_emojis.append(emoji)
        
        if not available_emojis:
            # 如果所有表情包都已下载，从所有表情包中选择
            available_emojis = self.emoji_data.copy()
        
        # 从可用表情包中随机选择一个，过滤最近使用的
        candidates = self.filter_recently_used(available_emojis)
        if not candidates:
            candidates = available_emojis  # 如果过滤后为空，使用全部
        
        # 随机选择
        if candidates:
            # 增加随机性：从候选中随机选择10-20个，再从中选择一个
            sample_size = min(20, len(candidates))
            sampled_candidates = random.sample(candidates, sample_size) if len(candidates) > sample_size else candidates
            selected = random.choice(sampled_candidates)
            
            logger.info(f"后备模式选择表情包: {selected.get('name')} (来自{len(candidates)}个候选)")
            
            # 尝试下载
            download_success = await self.download_single_emoji(selected)
            if download_success:
                self.add_to_recent_used(selected)
                logger.info(f"后备模式下载成功: {selected.get('name')}")
                return selected
            else:
                logger.warning(f"后备模式下载失败: {selected.get('name')}")
                return None
        
        return None
    
    def extract_emotion_from_filename(self, filename):
        """从文件名中提取情感关键词"""
        if not filename:
            return []
        
        # 常见的表情包文件名情感词汇
        emotion_keywords = {
            "开心": ["开心", "笑", "高兴", "快乐", "哈哈", "嘻嘻", "爽", "开森"],
            "可爱": ["可爱", "萌", "卖萌", "软萌", "呆萌", "小可爱", "kawaii"],
            "吃": ["吃", "美食", "饿", "香", "馋", "好吃", "味道", "食物", "饭", "菜"],
            "睡": ["睡", "困", "累", "休息", "梦", "床", "瞌睡"],
            "哭": ["哭", "泪", "伤心", "难过", "呜呜", "泪目"],
            "生气": ["生气", "愤怒", "气", "怒", "mad", "angry"],
            "惊讶": ["惊", "震惊", "哇", "意外", "surprised"],
            "疑问": ["疑问", "问号", "什么", "why", "confused"],
            "无语": ["无语", "无奈", "醉了", "服了", "speechless"],
            "害羞": ["害羞", "脸红", "不好意思", "shy"],
            "加油": ["加油", "努力", "fighting", "支持"],
            "谢谢": ["谢谢", "感谢", "thanks", "感激"],
            "对不起": ["对不起", "抱歉", "sorry", "道歉"],
            "游戏": ["游戏", "玩", "game", "play"],
            "工作": ["工作", "学习", "work", "study"],
            "思考": ["思考", "想", "thinking", "考虑"]
        }
        
        filename_lower = filename.lower()
        extracted_emotions