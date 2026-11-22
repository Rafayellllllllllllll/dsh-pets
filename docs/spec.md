# 制作与验证规范

本文件是 dsh-pets 的操作摘要。数字和字段以公开文档为准，这里不代替上游文档。

- 包格式（作者指南）：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/pets/README.md>
- 英文作者指南：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/pets/README.en.md>
- 用户指南（选择、悬浮窗、创意工坊）：<https://github.com/ccch1mneyyy/dsh-Go/blob/main/docs/guide/zh/06-pets.md>
- 作者指南写明硬限制来自上游 `app/tool/pet-pack.rules.json`（rule set revision 3）

投稿界面以用户指南为准。作者指南第 9 节仍写论坛账号登录，以及提交后等待审核才出现在「发现」。用户指南写的是 GitHub 登录，格式检查通过后直接出现在「发现」，有人举报后审核员才可能下架。

## 1. 完成状态

一只宠物在同时具备这些文件之前，都算未完成：

- 静态 PNG 图集
- 引用这些图集的 `pack.json`
- 打好的 zip
- 写明检查结果的说明

未完成时，该宠物的 `README.md` 必须包含「尚不可导入」。不要提供一个看起来能安装、实际缺图或未检查的 zip。

完成后删掉「尚不可导入」，改写实际检查结果：结构检查、包检查（若有脚本）和是否已在 App 里导入。没有在 App 里导入过，就不要写导入成功。

## 2. 最小包

```text
my-pet.zip
├─ pack.json
└─ idle.png
```

也可以是 `my-pet.zip` 里面一层 `my-pet/pack.json`。再深一层不识别。zip 里只能有一个 `pack.json`。

最小条件：

1. `schema` 为 `1`。
2. 有非空的 `id`、`canvas`（`w`/`h`）、`contentBox`（`x`/`y`/`w`/`h`）。
3. `animations` 至少一段，其中有名为 `idle` 的动画。
4. `states.idle` 是非空数组。
5. 图集是 `pack.json` 点名的静态 PNG。

本机导入可以不写 `author`、`license`、`source`。用户指南写明，投稿表单里只要有一个名称；英文名称、编号、作者、许可和来源可以留空。留空时，客户端分别用另一个名称、按名称生成的编号、GitHub 用户名、「作者授权」和 GitHub 主页。那是表单的默认填充，不是本仓库对角色作出的许可声明。

`docs/pack.skeleton.json` 只演示字段，仓库里没有它点名的 PNG，不能打包导入。

## 3. 硬限制

| 项 | 限制 |
| --- | --- |
| zip | ≤ 64 MiB；声明的展开总量也 ≤ 64 MiB；条目（含目录）≤ 256 |
| 客户端上传 | ≤ 50 MiB |
| `pack.json` | ≤ 256 KiB；UTF-8，无 BOM；JSON 深度 ≤ 64，导入门另限 60 层 |
| 图集数量 | ≤ 64 |
| 单张图 | 压缩后 ≤ 8 MiB；每边 ≤ 4096 px；解码缓冲 `w×h×4` ≤ 16 MiB |
| 全部图集 | ≤ 48 MiB |
| 图集文件名 | 无目录；以 `.png` 结尾；≤ 120 个 UTF-16 码元；不以 `.` 开头 |
| 帧 | `i` ≥ 0，`ms` ≥ 1，且都是整数 |
| `frameW` / `frameH` / `cols` | 1–4096 |
| `canvas` | 每边 1–2³⁰ |
| `scale` | 1–4096 的整数（导入门要求） |

zip 不加密，只用 store 或 deflate，无符号链接。路径不许绝对路径、反斜杠、NUL、冒号、单独的 `.` 或 `..`。文件名按 Unicode 15 NFC 与 casefold 比较，归一后重名会拒绝。除 `pack.json` 和它引用的 PNG 以外，其它文件会被丢掉。

## 4. 图集

- 每段动画使用一张平铺 PNG，多段动画可以共用一张。
- 格子等大，行优先：第 `i` 帧在 `col = i % cols`、`row = i ~/ cols`。
- 相同画面只保留一格，用 `frames[].i` 重复引用。
- 时长只看每帧的 `ms`。动画级 `ms` 不读。
- `frameW = trunc(box.w × scale)`，`frameH = trunc(box.h × scale)`。`box` 的宽高和 `scale` 都用整数最不容易错。
- `pixelArt: true` 且 `scale: 1` 时按最近邻绘制。
- 显示档位看 `contentBox.h`（小 22dp / 标准 30dp / 大 40dp），不看 `canvas`。
- `anchor.baseline` 是身体最低不透明行的下沿，阴影不要算进去。`anchor.x` 是身体中心，也是左右镜像轴。
- 预览默认用 `states.idle` 第一段动画的第一格。该格 `frameW`、`frameH` 都 ≤ 512，且乘积 ≤ 262144，才能做预览；超出时仍可关掉预览再投稿。

必须是完整静态 PNG：CRC 正确、IDAT 连续、zlib 流精确结束、无尾随数据。拒收 APNG（`acTL`、`fcTL`、`fdAT`）。

## 5. 状态与互动

App 有 11 个状态。除 `idle` 外都可以不画，没写的状态回落到 `states.idle` 的第一段动画。

| 状态 | 何时 |
| --- | --- |
| `idle` | 空闲。必需 |
| `thinking` | 等模型、推理、重试 |
| `working` | 工具运行或回复流式输出 |
| `juggling` | 子代理在跑 |
| `sweeping` | 上下文压缩 |
| `notification` | 有待确认或待回答的事 |
| `attention` | 一轮完成 |
| `error` | 一轮失败 |
| `carrying` | 预留。作者指南写明当前不会触发 |
| `sleeping` | 空闲且一段时间没有触摸，默认 60 秒 |
| `waking` | 睡着时被触摸或来了事件 |

事件优先级：`error` > `notification` > `sweeping` > `attention` > `carrying` = `juggling` > `working` > `thinking` > `idle` = `waking` > `sleeping`。

`reactions` 可写 `tapLeft`、`tapRight`、`tickle`、`drag`。作者指南：点左半或右半身体触发前两个；1.1 秒内连点 3 次是 `tickle`；拖动是 `drag`。`roam.anim` 只用于 App 内游走，悬浮窗不游走。

`pack.json` 不读 `description`。介绍文字写在宠物 README 和投稿表单里。

## 6. 打包

在已经有真实 PNG 和检查过的 `pack.json` 之后：

```sh
cd pets/<id>
zip -r <id>.zip pack.json *.png
```

zip 内需要一层文件夹时，先放进同名目录再打包。打包前确认没有把 `source/`、脚本、说明文件放进这个安装 zip；上游会丢掉多余文件，但仓库里的安装包应只保留清单和 PNG。

## 7. 检查

分三层。前一层通过，不代表后一层通过。

1. 仓库结构：`sh scripts/check-repo.sh`。它检查文档、源图哈希、未完成宠物的「尚不可导入」，以及有没有仓库级许可证或凭据文件。它不读图集几何。
2. 宠物包：等该宠物的 `scripts/` 里有验证脚本后再跑。脚本至少核对 `schema`、`states.idle`、PNG 签名、没有 APNG 控制块、`frameW`/`frameH` 与 `box × scale` 一致、文件大小没有超过第 3 节。不要把上游 `app/tool/make_pet_pack.py` 当成通用校验器。
3. App 导入：设置 → 宠物 → 选择器最后一格「导入」。同一 `id` 再导入会覆盖。作者指南写明，目前没有面向作者的独立校验 CLI，App 导入才是最终校验。

没有第 3 层的结果时，README 只写到已经做过的那一层。

## 8. 导入与投稿

导入路径以作者指南为准：设置 → 宠物 → 选择器最后一格「导入」。

投稿路径以用户指南为准：

1. 创意工坊 → 我的投稿 → 投稿。
2. 选一只已导入的宠物，或「从文件选择」直接交 zip。
3. 看一眼打好的包。
4. 勾选「我确认自己有权分享这份内容」后再交。

浏览和安装不用登录。投稿、查看自己的投稿和举报要使用 GitHub 登录。本仓库不代替维护者投稿。

工坊许可栏只接受作者指南列出的标识：`CC0-1.0`、`CC-BY-4.0`、`CC-BY-SA-4.0`、`MIT`、`Apache-2.0`、`BSD-2-Clause`、`BSD-3-Clause`、`LicenseRef-Author-Permission`。本仓库不替宠物选择其中任何一项。
