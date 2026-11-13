【原版 Flash 文件放置说明】
==============================

本目录用于存放「森林冰火人」原版 Flash 文件。

使用方法：
1. 将原版游戏文件命名为 fireboy.swf（注意扩展名必须是小写 .swf）
2. 放到本目录：games/swf/fireboy.swf
3. 在小游戏页面点击「森林冰火人 → 原版(Flash)」即可游玩

说明：
- 原版《森林冰火人》是 Adobe Flash 游戏，版权归原作者（Fridolin / Opiyo Games）
  所有。出于版权原因，本项目不内置、不提供该文件下载，请自行准备合法获得的
  原版文件（例如从你已购买或有授权的渠道获取）。
- 本项目通过开源的 Ruffle（https://ruffle.rs）在浏览器内模拟 Flash 运行时，
  无需安装任何插件。
- 若页面提示 "Incorrect response MIME type"，说明服务器未把 .wasm 识别为
  application/wasm——本项目已在 core/http.py 中配置好，正常部署即可。

常见问题：
- 打开后黑屏/无反应：先点击舞台中央让 Flash 获得键盘焦点；确认 swf 文件完整。
- 双人关卡按键：方向键 = 火娃，W/A/S/D = 冰娃。
