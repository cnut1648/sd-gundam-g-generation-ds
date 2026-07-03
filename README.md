# SD高达G世纪DS — 汉化构建工程

**SD Gundam G Generation DS**（任天堂DS，日版）汉化版的完整独立构建系统。
一条命令即可把日版卡带镜像转换为完整汉化 ROM，且逐字节可复现。

```
.
├── 0098 - SD Gundam G Generation DS (Japan).nds   # 日版原始 ROM（输入）
├── sd-gundam-g-generation-zh.nds                  # 汉化 ROM（构建输出）
├── build/          # 构建入口（build.py）—— 一条命令、单次流程
├── data/           # 全部汉化与构建数据（名称、对话、补丁、字库……）
├── utils/          # 辅助库（文本编解码、关卡构建、arm9 布局、ROM 读写）
├── test/           # 完整测试套件：静态门禁、模拟器实机、截图、VLM 判图
└── docs/           # 文档：构建指南、数据格式、地址表、经验教训
```

## 快速开始

```bash
# 首次准备
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 构建（输入日版 ROM，输出汉化 ROM）
.venv/bin/python build/build.py "0098 - SD Gundam G Generation DS (Japan).nds" sd-gundam-g-generation-zh.nds
```

预期输出：

```
[build] final ROM sha1 919eb5026501bdc757bbb304d2b02340e320a5b9  (MATCHES the shipped translation)
[build] wrote sd-gundam-g-generation-zh.nds  (30,324,584 bytes)
```

追加 `--pad32m 路径` 可同时输出补齐到 32 MiB 的镜像（部分烧录卡要求 2 的幂
大小；sha1 `f6d0a65c26c43b1a699dc0af2d029faeb097c5ef`）。

输入必须是 sha1 为 `12443b91297a57bcd2ace8da989c26ae635a79fd`（33,554,432
字节）的日版卡带镜像——构建会校验它以及 `data/manifest.json` 中记录的每个
中间组件哈希，输入错误或数据损坏都会立刻报错，绝不静默通过。

## 与原版游戏的差异（除汉化外）

在完整汉化之外，本版本相比日版原作仅有 **两处游戏性改动**：

1. **SP 关卡解锁条件放宽** —— 通关 24 关（24a/24b）之后进入 SP 系列关卡
   （以及后续各 SP 分支）的条件，由原版的 3～4 次索敌（自由战斗）降低为
   **只需 1 次索敌**（全部 7 处解锁判定统一修改）。
2. **永恒号搭载数提升** —— 战舰「永恒号」的搭载量由原版的 2 机改为标准的
   **6 机**。注意该数值为默认规格：已入手永恒号的旧存档沿用存档内既有的
   编组槽位，新入手的才按 6 机计。

两处改动均以数据形式收录并有文档记载：索敌次数见
`data/patches/code_patches.json`，永恒号搭载数见 `data/names/units.json`
（`carrier_capacity` 字段）；详情见 `docs/GAME_NOTES.md` 与
`docs/ROM_STRUCTURE.md`。

## 校验构建结果

```bash
.venv/bin/python test/run_static.py sd-gundam-g-generation-zh.nds             # 静态门禁，数秒
.venv/bin/python test/live/test_boot_render.py sd-gundam-g-generation-zh.nds  # 模拟器启动测试
```

完整的测试层级（静态 → 模拟器实机 → 截图基准 → VLM 判图）及其依赖见
`test/README.md`。

## 构建流程说明

单次确定性流程（详见 `docs/BUILD_GUIDE.md`）：

1. **代码段（arm9）** —— 将汉化后的名称表（机体、武器、驾驶员、ID指令、
   能力、部件）、界面标签、文本宏字典、字符串池及剧情/作战简报文本写入
   日版镜像；应用约 36 处有文档记载的代码补丁（渲染路径修正与游戏性
   调整）；将 12×12 中文字库图集和两个重定位字符串库作为开机自动加载
   数据追加到镜像尾部。
2. **关卡对话** —— 重建全部 101 个 `_STG*.bin` 关卡文件：植入汉化对话块
   （允许增长）、重定位所有绝对指针、保持文件头各表 4 字节对齐。
3. **数据文件** —— 重建 20 个杂项文件：战斗喊话、必杀技名台词、效果/能力
   文本、图鉴介绍、部件名称，以及少量重绘的界面图块。
4. **容器组装** —— 重新组装 ROM 并校验最终 sha1。

## 文档索引

| 文档 | 内容 |
|---|---|
| `docs/BUILD_GUIDE.md` | 构建流程逐步讲解、组件管线、如何修改一条翻译 |
| `docs/DATA_FORMATS.md` | `data/` 下全部数据的格式说明 |
| `docs/ROM_STRUCTURE.md` | NDS 容器、arm9 内存布局、自动加载机制、**完整地址表** |
| `docs/TEXT_SYSTEM.md` | 文本编码、字库图集、渲染器、字典、宽度预算 |
| `docs/STAGE_FORMAT.md` | 关卡文件格式：对话块、指针、增长、对齐 |
| `docs/GAME_NOTES.md` | 游戏结构，以及每类文本各自存放的位置 |
| `docs/TRANSLATION_GUIDE.md` | 翻译规范、术语表方法、QA 流程 |
| `docs/TESTING_APPROACH.md` | 测试思想：静态门禁、实机测试、VLM 判图 |
| `docs/LESSONS_LEARNED.md` | 弯路目录：被推翻的判断、崩溃案例及对应防护 |
| `data/README.md` | 数据目录布局与格式速览 |
| `test/README.md` | 各测试层级的运行方法 |

## 环境要求

* Python ≥ 3.12，构建仅需 `ndspy`；测试另需 `Pillow`/`numpy`。
* 实机测试还需 melonDS、Xvfb 与 xdotool —— 见 `test/README.md`。

## 版权说明

请自行转储持有的日版卡带作为输入。本仓库仅包含汉化数据、工具与文档，
不分发任何 ROM 镜像（`.nds` 文件已被 `.gitignore` 排除）。
