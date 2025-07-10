<Readme.md>
## NapCat戳一戳插件  version 0.3.3

这个插件设计的目的是想让麦麦具有主动戳别人的功能。

插件配置需要在NapCat中新建HTTP服务器，并且关闭CORS和Websocket，设置地址为localhost，端口为4999。
		（超小声bb）虽然Adapter在写这个版本时候已经开放了poke的隧道，但是我搞不明白（死掉)

墙裂建议在更新前备份插件



更新日志：
		<version 0.1.0>:
						构建了代码框架
						分离私聊戳戳和群聊戳戳的请求
						使用HTTP直接与Napcat对接
						强制启用DEBUG模式
		
		<version 0.3.3>:
						修改了API格式，支持到了0.8.1版本
						添加了manifest文件
		
		